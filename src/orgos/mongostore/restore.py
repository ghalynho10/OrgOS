"""The edge of `orgos mongo restore`: stream the dump into mongorestore, check, mark.

The archive goes from the zip member straight into mongorestore's stdin
inside the mongo container, so no extracted copy is ever written. Nothing
writes to `JiraReposAnon` except that mongorestore; `--reset` is the only drop.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
import zipfile
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import IO, Any

from pymongo import MongoClient

from orgos.config import Settings
from orgos.mongostore import MongoStoreError, mongo_errors
from orgos.mongostore.connect import root_client, secrets
from orgos.mongostore.dump import ARCHIVE_MEMBER, DATABASE, EXPECTED_COUNTS
from orgos.mongostore.marker import (
    Marker,
    delete_marker,
    ensure_reader,
    live_counts,
    write_marker,
)
from orgos.mongostore.preflight import (
    DEFAULT_LIMITS,
    Facts,
    Limits,
    blocking_message,
    evaluate,
    gather_facts,
    log_results,
)
from orgos.mongostore.verify import compare, failure_message, report_text

log = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
OUT_OF_MEMORY_STATUS = 137

# Runs inside the container, whose shell expands its own root credentials,
# so neither password is ever on the host command line.
RESTORE_SCRIPT = (
    "exec mongorestore"
    ' --username="$MONGO_INITDB_ROOT_USERNAME"'
    ' --password="$MONGO_INITDB_ROOT_PASSWORD"'
    " --authenticationDatabase=admin"
    " --gzip --archive"
    f' --nsInclude="{DATABASE}.*"'
    " --stopOnError"
)


def mongorestore_argv(container: str) -> tuple[str, ...]:
    """The host side command line that runs mongorestore in `container`."""
    return ("docker", "exec", "-i", container, "sh", "-c", RESTORE_SCRIPT)


def mongorestore_failure(status: int) -> str:
    """The message for a non zero mongorestore exit."""
    message = f"mongorestore exited with status {status}"
    if status == OUT_OF_MEMORY_STATUS:
        message += " (the container probably ran out of memory)"
    return f"{message}; {DATABASE} is partial, rerun with --reset"


def _chunks(zip_path: Path, member: str) -> Iterator[bytes]:
    # Reading the member to its end makes zipfile check the CRC.
    try:
        with zipfile.ZipFile(zip_path) as archive, archive.open(member) as source:
            while chunk := source.read(CHUNK_SIZE):
                yield chunk
    except KeyError:
        raise MongoStoreError(f"{zip_path.name} has no member {member!r}") from None


def _feed(chunks: Iterator[bytes], stdin: IO[bytes]) -> None:
    try:
        for chunk in chunks:
            stdin.write(chunk)
    except BrokenPipeError:
        # mongorestore stopped reading (an error under --stopOnError); its exit status says why.
        pass
    finally:
        with contextlib.suppress(BrokenPipeError):
            stdin.close()


def stream(container: str, zip_path: Path, member: str) -> None:
    """Pipe the archive member into mongorestore in `container`, raising on any failure.

    mongorestore's output goes to this process's stderr. A CRC error, a non
    zero exit, or Ctrl-C each raise `MongoStoreError` and leave the database partial.
    """
    child = subprocess.Popen(mongorestore_argv(container), stdin=subprocess.PIPE, stdout=2)
    assert child.stdin is not None
    try:
        _feed(_chunks(zip_path, member), child.stdin)
    except KeyboardInterrupt:
        child.kill()
        child.wait()
        raise MongoStoreError(
            f"interrupted: {DATABASE} is partial, rerun `orgos mongo restore --reset`"
        ) from None
    except zipfile.BadZipFile as error:
        child.wait()
        raise MongoStoreError(
            f"the archive failed its zip check ({error}); {DATABASE} is partial, rerun with --reset"
        ) from None
    except BaseException:
        child.kill()
        child.wait()
        raise
    status = child.wait()
    if status != 0:
        raise MongoStoreError(mongorestore_failure(status))


def _skip(client: MongoClient[dict[str, Any]], settings: Settings, marker: Marker) -> None:
    sys.stdout.write(report_text(compare(marker.counts, live_counts(client))))
    ensure_reader(client, settings.mongo_reader_user, settings.mongo_reader_password)
    log.info("already restored (marker from %s), nothing to do", marker.completed_at.isoformat())


def _restore(
    client: MongoClient[dict[str, Any]],
    settings: Settings,
    facts: Facts,
    *,
    reset: bool,
    expected: Mapping[str, int],
    member: str,
) -> None:
    if reset:
        delete_marker(client)
        client.drop_database(DATABASE)
        log.info("reset: deleted the marker, then dropped %s", DATABASE)
    # Every check passed, so these facts were gathered.
    container, receipt = facts.container_id, facts.receipt.value
    header, server_version = facts.header.value, facts.server_version.value
    assert container is not None and receipt is not None
    assert header is not None and server_version is not None
    stream(container, settings.data_dir / receipt.file, member)
    live = live_counts(client)
    comparison = compare(expected, live)
    sys.stdout.write(report_text(comparison))
    if not comparison.passed:
        raise MongoStoreError(failure_message(comparison, "the expected counts"))
    write_marker(
        client,
        Marker(
            completed_at=datetime.now(UTC),
            receipt=MappingProxyType(receipt.as_dict()),
            dump_server_version=header.server_version,
            dump_tool_version=header.tool_version,
            server_version=server_version,
            counts=MappingProxyType(live),
        ),
    )
    ensure_reader(client, settings.mongo_reader_user, settings.mongo_reader_password)
    log.info("restore complete: %d collections verified, marker written", len(live))


def restore(
    settings: Settings,
    *,
    reset: bool,
    check_only: bool,
    container: str | None = None,
    expected: Mapping[str, int] = EXPECTED_COUNTS,
    member: str = ARCHIVE_MEMBER,
    limits: Limits = DEFAULT_LIMITS,
) -> None:
    """Check, then restore `JiraReposAnon` from the zip the receipt names.

    Skips when a marker matches the live database, refuses partial data
    without `--reset`, and writes the marker only on PASS. `container=None`
    finds the compose mongo container by its labels.
    """
    hidden = secrets(settings)
    with mongo_errors("restore", hidden), root_client(settings) as client:
        facts = gather_facts(
            client,
            reset=reset,
            data_dir=settings.data_dir,
            container=container,
            member=member,
            secrets=hidden,
        )
        results = evaluate(facts, reset, expected, limits)
        log_results(results)
        blocked = blocking_message(results)
        if blocked is not None:
            raise MongoStoreError(blocked)
        if facts.complete and not reset:
            assert facts.marker is not None
            if check_only:
                log.info("check: %s is already restored, a plain run would skip", DATABASE)
            else:
                _skip(client, settings, facts.marker)
            return
        if check_only:
            action = "reset and restore" if reset else "restore"
            log.info("check: no check blocks, a plain run would %s", action)
            return
        _restore(client, settings, facts, reset=reset, expected=expected, member=member)
