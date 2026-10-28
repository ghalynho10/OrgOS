"""The checks that run before anything is written to Mongo, so an hour long restore fails fast.

The edge gathers each fact as a `Fact` (a value, a reason it could not be
had, or neither when it was not needed); the pure `evaluate` turns facts into
one result per check. A fact that could not be gathered shows as "not
checked" and never blocks twice. Every block is listed together.
"""

from __future__ import annotations

import enum
import gzip
import hashlib
import logging
import re
import subprocess
import zipfile
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from orgos.mongostore import MongoStoreError, describe, redact
from orgos.mongostore.dump import (
    COMPOSE_PROJECT,
    COMPOSE_SERVICE,
    DATABASE,
    HEADER_LIMIT,
    DumpHeader,
    TruncatedHeader,
    parse_header,
)
from orgos.mongostore.marker import Marker, live_counts, read_marker, reclaimable_bytes
from orgos.mongostore.receipt import RECEIPT_NAME, Receipt, parse_receipt
from orgos.mongostore.verify import compare

log = logging.getLogger(__name__)

GB = 10**9
DOCKER_TIMEOUT_S = 60
HEADER_STEP = 64 * 1024
MD5_CHUNK = 8 * 1024 * 1024
TOOLS_VERSION = re.compile(r"^mongorestore version: (\S+)", re.MULTILINE)
LABELS = (
    f"label=com.docker.compose.project={COMPOSE_PROJECT}",
    f"label=com.docker.compose.service={COMPOSE_SERVICE}",
)
# Runs inside the container with its own root credentials; mongosh fails
# to connect when they are wrong.
CONTAINER_LOGIN_SCRIPT = (
    "exec mongosh --quiet"
    ' --username="$MONGO_INITDB_ROOT_USERNAME"'
    ' --password="$MONGO_INITDB_ROOT_PASSWORD"'
    " --authenticationDatabase=admin"
    ' --eval "db.runCommand({ping: 1}).ok"'
)

CheckStatus = Literal["PASS", "WARN", "BLOCK", "SKIP"]


@dataclass(frozen=True)
class Limits:
    """The thresholds a restore must meet, relaxed only by tests."""

    min_free_disk: int = 23 * GB
    min_memory: int = 4 * GB


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class Fact[T]:
    """A gathered value, or the reason it could not be had; neither when not needed."""

    value: T | None = None
    error: str | None = None


class State(enum.Enum):
    """The state of `JiraReposAnon`, from its data and its marker."""

    ABSENT = "absent"
    PARTIAL = "partial"
    COMPLETE = "complete"
    DRIFT = "drift"


@dataclass(frozen=True)
class ZipFacts:
    """The zip on disk; `md5` is `None` when the size already disagrees."""

    size: int
    md5: str | None


@dataclass(frozen=True)
class Facts:
    """Everything the preflight looks at, gathered at the edge."""

    containers: Fact[tuple[str, ...]] = Fact()
    container_given: bool = False
    tools_version: Fact[str] = Fact()
    container_login: Fact[str] = Fact()
    server_version: Fact[str] = Fact()
    state: Fact[State] = Fact()
    marker: Marker | None = None
    receipt: Fact[Receipt] = Fact()
    zip: Fact[ZipFacts] = Fact()
    header: Fact[DumpHeader] = Fact()
    free_disk: Fact[int] = Fact()
    reclaimable: Fact[int] = Fact()
    other_containers: Fact[tuple[str, ...]] = Fact()
    memory: Fact[int] = Fact()

    @property
    def container_id(self) -> str | None:
        ids = self.containers.value
        return ids[0] if ids is not None and len(ids) == 1 else None

    @property
    def complete(self) -> bool:
        return self.state.value is State.COMPLETE


@dataclass(frozen=True)
class CheckResult:
    """One preflight line: `<status> TAB <check> TAB <value>`."""

    status: CheckStatus
    name: str
    value: str


# Pure: work out the state, parse docker output, evaluate the checks.


def work_out_state(marker_counts: Mapping[str, int] | None, live: Mapping[str, int]) -> State:
    """ABSENT, PARTIAL, COMPLETE or DRIFT from the marker's counts and the live ones."""
    if marker_counts is None:
        return State.PARTIAL if live else State.ABSENT
    return State.COMPLETE if compare(marker_counts, live).passed else State.DRIFT


def parse_container_ids(text: str) -> tuple[str, ...]:
    """The ids `docker ps -q` printed, one per line."""
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def parse_other_containers(text: str) -> tuple[str, ...]:
    """Names of running containers whose compose project is not `orgos` (unlabelled included)."""
    names = []
    for line in text.splitlines():
        if not line.strip():
            continue
        name, _, project = line.partition("\t")
        if project.strip() != COMPOSE_PROJECT:
            names.append(name.strip())
    return tuple(names)


def parse_df_free_bytes(text: str) -> int:
    """Free bytes from `df -Pk`: field 4 of line 2, in KiB."""
    lines = text.splitlines()
    if len(lines) < 2 or len(lines[1].split()) < 4 or not lines[1].split()[3].isdigit():
        raise ValueError(f"unexpected df output: {text.strip()!r}")
    return int(lines[1].split()[3]) * 1024


def parse_tools_version(text: str) -> str:
    """The version from `mongorestore --version`."""
    match = TOOLS_VERSION.search(text)
    if match is None:
        raise ValueError(f"no version in mongorestore output: {text.strip()[:80]!r}")
    return match.group(1)


def _part(version: str, count: int) -> str:
    return ".".join(version.split(".")[:count])


def _gb(value: int) -> str:
    # Rounded down, so a value under a limit never prints as the limit.
    return f"{value * 10 // GB / 10:.1f} GB"


def _not_checked(name: str, needs: str) -> CheckResult:
    return CheckResult("SKIP", name, f"not checked (needs {needs})")


def _container_checks(facts: Facts) -> list[CheckResult]:
    results = []
    ids, error = facts.containers.value, facts.containers.error
    if ids is None:
        results.append(CheckResult("BLOCK", "container", error or "not looked up"))
    elif len(ids) == 1:
        results.append(
            CheckResult("PASS", "container", ids[0] + (" (given)" if facts.container_given else ""))
        )
    elif not ids:
        results.append(
            CheckResult(
                "BLOCK",
                "container",
                f"no running mongo container labelled project {COMPOSE_PROJECT}, service "
                f"{COMPOSE_SERVICE} (start it: docker compose --profile staging up -d --wait)",
            )
        )
    else:
        results.append(
            CheckResult(
                "BLOCK", "container", f"{len(ids)} containers carry the labels: {', '.join(ids)}"
            )
        )
    for name, fact in (
        ("mongorestore", facts.tools_version),
        ("container login", facts.container_login),
    ):
        if fact.error is not None:
            results.append(CheckResult("BLOCK", name, fact.error))
        elif fact.value is None:
            results.append(_not_checked(name, "container"))
        else:
            results.append(CheckResult("PASS", name, fact.value))
    return results


def _host_checks(facts: Facts, reset: bool) -> list[CheckResult]:
    results = []
    if facts.server_version.value is not None:
        results.append(CheckResult("PASS", "host login", f"server {facts.server_version.value}"))
    else:
        results.append(
            CheckResult("BLOCK", "host login", facts.server_version.error or "not tried")
        )
    state, error = facts.state.value, facts.state.error
    if error is not None:
        results.append(CheckResult("BLOCK", "state", error))
    elif state is None:
        results.append(_not_checked("state", "host login"))
    elif state is State.ABSENT:
        results.append(
            CheckResult("PASS", "state", f"{DATABASE} is absent, a restore starts fresh")
        )
    elif state is State.COMPLETE:
        action = "--reset restores it again" if reset else "a plain run skips"
        results.append(CheckResult("PASS", "state", f"{DATABASE} is complete, {action}"))
    else:
        what = (
            "has data but no restore record"
            if state is State.PARTIAL
            else "differs from its restore record (or is gone)"
        )
        if reset:
            results.append(CheckResult("PASS", "state", f"{DATABASE} {what}, --reset drops it"))
        else:
            results.append(
                CheckResult(
                    "BLOCK",
                    "state",
                    f"{DATABASE} {what}; rerun with --reset to drop it and restore",
                )
            )
    return results


def _restore_checks(
    facts: Facts, reset: bool, expected: Mapping[str, int], limits: Limits
) -> list[CheckResult]:
    if facts.complete and not reset:
        return [
            CheckResult("SKIP", name, "not needed, already restored")
            for name in ("receipt", "zip", "disk", "header")
        ]
    results = []
    receipt = facts.receipt.value
    if receipt is None:
        results.append(CheckResult("BLOCK", "receipt", facts.receipt.error or "not read"))
    else:
        results.append(CheckResult("PASS", "receipt", receipt.file))

    zip_facts = facts.zip.value
    if facts.zip.error is not None:
        results.append(CheckResult("BLOCK", "zip", facts.zip.error))
    elif zip_facts is None or receipt is None:
        results.append(_not_checked("zip", "receipt"))
    elif zip_facts.size != receipt.size:
        results.append(
            CheckResult(
                "BLOCK", "zip", f"size {zip_facts.size} differs from the receipt's {receipt.size}"
            )
        )
    elif zip_facts.md5 != receipt.md5:
        results.append(
            CheckResult(
                "BLOCK", "zip", f"md5 {zip_facts.md5} differs from the receipt's {receipt.md5}"
            )
        )
    else:
        results.append(
            CheckResult("PASS", "zip", f"size {zip_facts.size} and md5 {zip_facts.md5} match")
        )

    free = facts.free_disk.value
    if facts.free_disk.error is not None:
        results.append(CheckResult("BLOCK", "disk", facts.free_disk.error))
    elif free is None:
        results.append(_not_checked("disk", "container"))
    else:
        credit = (facts.reclaimable.value or 0) if reset else 0
        shown = f"{_gb(free)} free" + (
            f" plus {_gb(credit)} reclaimable by --reset" if credit else ""
        )
        if free + credit >= limits.min_free_disk:
            results.append(CheckResult("PASS", "disk", shown))
        else:
            results.append(
                CheckResult(
                    "BLOCK",
                    "disk",
                    f"Docker has {shown} on the Mongo volume, under {_gb(limits.min_free_disk)}: "
                    f"a restore needs at least {_gb(limits.min_free_disk)} free there",
                )
            )

    header = facts.header.value
    if facts.header.error is not None:
        results.append(CheckResult("BLOCK", "header", facts.header.error))
    elif header is None:
        results.append(_not_checked("header", "zip"))
    else:
        problems = []
        missing = sorted(set(expected) - header.collections)
        extra = sorted(header.collections - set(expected))
        if missing or extra:
            problems.append(
                "collection names differ from the expected"
                + (f", missing {', '.join(missing)}" if missing else "")
                + (f", unexpected {', '.join(extra)}" if extra else "")
            )
        if header.databases != {DATABASE}:
            problems.append(f"names database {', '.join(sorted(header.databases))}, not {DATABASE}")
        running = facts.server_version.value
        if running is not None and _part(running, 1) != _part(header.server_version, 1):
            problems.append(
                f"dumped from server {header.server_version}, "
                f"but Mongo runs {running} (major differs)"
            )
        if problems:
            results.append(CheckResult("BLOCK", "header", "; ".join(problems)))
        else:
            results.append(
                CheckResult(
                    "PASS",
                    "header",
                    f"server {header.server_version}, tools {header.tool_version}, "
                    f"{len(header.entries)} collections: {', '.join(sorted(header.collections))}",
                )
            )
    return results


def _dump_versions(facts: Facts) -> tuple[str, str] | None:
    if facts.header.value is not None:
        return facts.header.value.server_version, facts.header.value.tool_version
    if facts.marker is not None:
        return facts.marker.dump_server_version, facts.marker.dump_tool_version
    return None


def _warnings(facts: Facts, limits: Limits) -> list[CheckResult]:
    results = []
    others = facts.other_containers.value
    if others is None:
        results.append(_not_checked("other containers", "docker"))
    elif others:
        results.append(
            CheckResult(
                "WARN",
                "other containers",
                f"running, stop them to free memory: {', '.join(others)}",
            )
        )
    else:
        results.append(CheckResult("PASS", "other containers", "none"))

    memory = facts.memory.value
    if memory is None:
        results.append(_not_checked("memory", "docker"))
    elif memory < limits.min_memory:
        results.append(
            CheckResult(
                "WARN", "memory", f"Docker has {_gb(memory)}, under {_gb(limits.min_memory)}"
            )
        )
    else:
        results.append(CheckResult("PASS", "memory", _gb(memory)))

    dump = _dump_versions(facts)
    running, tools = facts.server_version.value, facts.tools_version.value
    if dump is None or running is None:
        results.append(_not_checked("server minor", "header and host login"))
    elif _part(running, 2) != _part(dump[0], 2):
        results.append(
            CheckResult("WARN", "server minor", f"Mongo runs {running}, the dump is from {dump[0]}")
        )
    else:
        results.append(CheckResult("PASS", "server minor", f"{running} and dump {dump[0]}"))
    if dump is None or tools is None:
        results.append(_not_checked("tools major", "header and mongorestore"))
    elif _part(tools, 1) != _part(dump[1], 1):
        results.append(
            CheckResult("WARN", "tools major", f"mongorestore {tools}, the dump used {dump[1]}")
        )
    else:
        results.append(CheckResult("PASS", "tools major", f"{tools} and dump {dump[1]}"))
    return results


def evaluate(
    facts: Facts, reset: bool, expected: Mapping[str, int], limits: Limits
) -> tuple[CheckResult, ...]:
    """One result per check, in the spec's order, from facts alone."""
    return tuple(
        _container_checks(facts)
        + _host_checks(facts, reset)
        + _restore_checks(facts, reset, expected, limits)
        + _warnings(facts, limits)
    )


def blocking_message(results: tuple[CheckResult, ...]) -> str | None:
    """One message listing every BLOCK, or `None` when nothing blocks."""
    blocks = [result for result in results if result.status == "BLOCK"]
    if not blocks:
        return None
    lines = [f"  {result.name}: {result.value}" for result in blocks]
    return f"restore blocked by {len(blocks)} check(s), nothing was written:\n" + "\n".join(lines)


def log_results(results: tuple[CheckResult, ...]) -> None:
    """Log each result: WARN at WARNING, BLOCK at ERROR, the rest at INFO."""
    levels = {"WARN": logging.WARNING, "BLOCK": logging.ERROR}
    for result in results:
        log.log(
            levels.get(result.status, logging.INFO),
            "%s\t%s\t%s",
            result.status,
            result.name,
            result.value,
        )


# The edge: gather facts from Docker, Mongo, and the data folder.


def _docker(*args: str, secrets: tuple[SecretStr, ...] = ()) -> Fact[str]:
    argv = ("docker", *args)
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S, check=False
        )
    except FileNotFoundError:
        return Fact(error="docker is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return Fact(error=f"`docker {args[0]}` did not answer in {DOCKER_TIMEOUT_S} s")
    if done.returncode != 0:
        reason = (done.stderr.strip() or done.stdout.strip()).splitlines()[-1:] or ["no output"]
        return Fact(
            error=f"`docker {args[0]}` exited {done.returncode}: {redact(reason[0], secrets)}"
        )
    return Fact(value=done.stdout)


def _parsed[T](fact: Fact[str], parse: Callable[[str], T]) -> Fact[T]:
    if fact.value is None:
        return Fact(error=fact.error)
    try:
        value = parse(fact.value)
    except ValueError as error:
        return Fact(error=str(error))
    return Fact(value=value)


def find_container(secrets: tuple[SecretStr, ...] = ()) -> Fact[tuple[str, ...]]:
    """Ids of running containers labelled as the compose mongo service."""
    return _parsed(
        _docker("ps", "-q", "--filter", LABELS[0], "--filter", LABELS[1], secrets=secrets),
        parse_container_ids,
    )


def file_md5(path: Path) -> str:
    """The md5 of a file, read in chunks."""
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as file:
        while chunk := file.read(MD5_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(zip_path: Path, member: str) -> DumpHeader:
    """Parse the archive header from the zip member, decompressing at most 4 MB."""
    try:
        with (
            zipfile.ZipFile(zip_path) as archive,
            archive.open(member) as raw,
            gzip.GzipFile(fileobj=raw) as source,
        ):
            data = b""
            while len(data) < HEADER_LIMIT:
                chunk = source.read(min(HEADER_STEP, HEADER_LIMIT - len(data)))
                data += chunk
                try:
                    return parse_header(data)
                except TruncatedHeader:
                    if not chunk:
                        raise
            raise TruncatedHeader(f"no archive header end in the first {HEADER_LIMIT} bytes")
    except KeyError:
        raise MongoStoreError(f"{zip_path.name} has no member {member!r}") from None


def _gather_files(
    data_dir: Path, member: str
) -> tuple[Fact[Receipt], Fact[ZipFacts], Fact[DumpHeader]]:
    path = data_dir / RECEIPT_NAME
    try:
        receipt = parse_receipt(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (
            Fact(error=f"no {RECEIPT_NAME} in {data_dir.resolve()} (run scripts/fetch_data.sh)"),
            Fact(),
            Fact(),
        )
    except (MongoStoreError, OSError, UnicodeDecodeError) as error:
        return Fact(error=f"{path} is malformed: {error}"), Fact(), Fact()
    zip_path = data_dir / receipt.file
    try:
        size = zip_path.stat().st_size
    except FileNotFoundError:
        return (
            Fact(value=receipt),
            Fact(error=f"the zip the receipt names is missing: {zip_path}"),
            Fact(),
        )
    except OSError as error:
        return Fact(value=receipt), Fact(error=f"cannot read {zip_path}: {error}"), Fact()
    try:
        md5 = file_md5(zip_path) if size == receipt.size else None
    except OSError as error:
        return Fact(value=receipt), Fact(error=f"cannot read {zip_path}: {error}"), Fact()
    try:
        header: Fact[DumpHeader] = Fact(value=read_header(zip_path, member))
    except (MongoStoreError, zipfile.BadZipFile, zlib.error, OSError, EOFError) as error:
        header = Fact(error=f"cannot read the archive header: {error}")
    return Fact(value=receipt), Fact(value=ZipFacts(size, md5)), header


def gather_facts(
    client: MongoClient[dict[str, Any]],
    *,
    reset: bool,
    data_dir: Path,
    container: str | None,
    member: str,
    secrets: tuple[SecretStr, ...],
) -> Facts:
    """Gather every fact the checks need; failures become reasons, never crashes."""
    containers = Fact(value=(container,)) if container is not None else find_container(secrets)
    ids = containers.value
    container_id = ids[0] if ids is not None and len(ids) == 1 else None

    tools: Fact[str] = Fact()
    login: Fact[str] = Fact()
    free: Fact[int] = Fact()
    if container_id is not None:
        tools = _parsed(
            _docker("exec", container_id, "mongorestore", "--version", secrets=secrets),
            parse_tools_version,
        )
        login = _parsed(
            _docker("exec", container_id, "sh", "-c", CONTAINER_LOGIN_SCRIPT, secrets=secrets),
            lambda _: "ok",
        )

    server: Fact[str] = Fact()
    state: Fact[State] = Fact()
    marker: Marker | None = None
    try:
        server = Fact(value=str(client.admin.command("buildInfo")["version"]))
        marker = read_marker(client)
        live = live_counts(client)
        state = Fact(value=work_out_state(None if marker is None else marker.counts, live))
    except PyMongoError as error:
        reason = describe(error, secrets)
        if server.value is None:
            server = Fact(error=f"login to Mongo from this machine failed: {reason}")
        else:
            state = Fact(error=f"reading {DATABASE} failed: {reason}")

    receipt: Fact[Receipt] = Fact()
    zip_facts: Fact[ZipFacts] = Fact()
    header: Fact[DumpHeader] = Fact()
    reclaimable: Fact[int] = Fact()
    if not (state.value is State.COMPLETE and not reset):
        receipt, zip_facts, header = _gather_files(data_dir, member)
        if container_id is not None:
            free = _parsed(
                _docker("exec", container_id, "df", "-Pk", "/data/db", secrets=secrets),
                parse_df_free_bytes,
            )
        if reset and server.value is not None:
            try:
                reclaimable = Fact(value=reclaimable_bytes(client))
            except PyMongoError as error:
                reclaimable = Fact(error=describe(error, secrets))

    others = _parsed(
        _docker(
            "ps",
            "--format",
            '{{.Names}}\t{{.Label "com.docker.compose.project"}}',
            secrets=secrets,
        ),
        parse_other_containers,
    )
    memory = _parsed(
        _docker("info", "--format", "{{.MemTotal}}", secrets=secrets),
        lambda text: int(text.strip()),
    )
    return Facts(
        containers=containers,
        container_given=container is not None,
        tools_version=tools,
        container_login=login,
        server_version=server,
        state=state,
        marker=marker,
        receipt=receipt,
        zip=zip_facts,
        header=header,
        free_disk=free,
        reclaimable=reclaimable,
        other_containers=others,
        memory=memory,
    )
