"""The preflight as a table: one fact changed per case, through the pure `evaluate`."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from orgos.mongostore.dump import DATABASE, DumpHeader, HeaderEntry
from orgos.mongostore.marker import Marker
from orgos.mongostore.preflight import (
    GB,
    CheckResult,
    Fact,
    Facts,
    Limits,
    State,
    ZipFacts,
    blocking_message,
    evaluate,
    log_results,
    parse_container_ids,
    parse_df_free_bytes,
    parse_other_containers,
    parse_tools_version,
    work_out_state,
)
from orgos.mongostore.receipt import Receipt

EXPECTED = {"Alpha": 3, "Beta": 2}
LIMITS = Limits()
RECEIPT = Receipt(
    "tiny.zip", 100, "a" * 32, "1", "v", "http://x/?download=1", "2026-10-26T12:00:00Z"
)
HEADER = DumpHeader(
    "7.0.14",
    "100.9.5",
    (HeaderEntry(DATABASE, "Alpha", "collection"), HeaderEntry(DATABASE, "Beta", "collection")),
)
MARKER = Marker(
    completed_at=datetime(2026, 10, 26, tzinfo=UTC),
    receipt=MappingProxyType(RECEIPT.__dict__),
    dump_server_version="7.0.14",
    dump_tool_version="100.9.5",
    server_version="7.0.45",
    counts=MappingProxyType(EXPECTED),
)
# A fresh restore with every check passing.
GOOD = Facts(
    containers=Fact(value=("abc123",)),
    tools_version=Fact(value="100.19.1"),
    container_login=Fact(value="ok"),
    server_version=Fact(value="7.0.45"),
    state=Fact(value=State.ABSENT),
    receipt=Fact(value=RECEIPT),
    zip=Fact(value=ZipFacts(100, "a" * 32)),
    header=Fact(value=HEADER),
    free_disk=Fact(value=100 * GB),
    other_containers=Fact(value=()),
    memory=Fact(value=8 * GB),
)
COMPLETE = replace(
    GOOD,
    state=Fact(value=State.COMPLETE),
    marker=MARKER,
    receipt=Fact(),
    zip=Fact(),
    header=Fact(),
    free_disk=Fact(),
)


def _results(facts: Facts, *, reset: bool = False) -> dict[str, CheckResult]:
    return {result.name: result for result in evaluate(facts, reset, EXPECTED, LIMITS)}


def _blocks(facts: Facts, *, reset: bool = False) -> dict[str, str]:
    return {r.name: r.value for r in _results(facts, reset=reset).values() if r.status == "BLOCK"}


def test_a_good_fresh_restore_blocks_nothing() -> None:
    results = _results(GOOD)
    assert {result.status for result in results.values()} == {"PASS"}
    assert blocking_message(tuple(results.values())) is None
    assert "Alpha, Beta" in results["header"].value


# covers: AC-13
@pytest.mark.parametrize(
    ("change", "name", "reason"),
    [
        (
            {"receipt": Fact(error="no dataset.receipt in /d"), "zip": Fact()},
            "receipt",
            "no dataset",
        ),
        ({"receipt": Fact(error="/d/dataset.receipt is malformed: x")}, "receipt", "malformed"),
        ({"zip": Fact(error="the zip the receipt names is missing")}, "zip", "missing"),
        ({"zip": Fact(value=ZipFacts(99, None))}, "zip", "size 99 differs from the receipt's 100"),
        ({"zip": Fact(value=ZipFacts(100, "b" * 32))}, "zip", "md5 bbbb"),
    ],
)
def test_receipt_and_zip_problems_block(change: dict[str, object], name: str, reason: str) -> None:
    blocks = _blocks(replace(GOOD, **change))  # type: ignore[arg-type]
    assert reason in blocks[name]


# covers: AC-14
def test_low_disk_blocks_with_the_measured_space_and_the_fix() -> None:
    blocks = _blocks(replace(GOOD, free_disk=Fact(value=79_999_999_999)))
    assert "Docker has 79.9 GB free on the Mongo volume, under 80.0 GB" in blocks["disk"]
    assert "raise the Docker disk limit to at least 128 GB" in blocks["disk"]


# covers: AC-14
def test_reset_counts_the_existing_database_as_free() -> None:
    low = replace(GOOD, free_disk=Fact(value=50 * GB), reclaimable=Fact(value=40 * GB))
    assert "disk" in _blocks(low)
    results = _results(replace(low, state=Fact(value=State.PARTIAL)), reset=True)
    assert results["disk"].status == "PASS"
    assert "40.0 GB reclaimable by --reset" in results["disk"].value


# covers: AC-15
@pytest.mark.parametrize(
    ("ids", "reason"),
    [((), "no running mongo container"), (("a", "b"), "2 containers carry the labels: a, b")],
)
def test_none_or_several_containers_block(ids: tuple[str, ...], reason: str) -> None:
    facts = replace(GOOD, containers=Fact(value=ids), tools_version=Fact(), container_login=Fact())
    blocks = _blocks(facts)
    assert reason in blocks["container"]
    assert "mongorestore" not in blocks
    assert _results(facts)["mongorestore"].value == "not checked (needs container)"


# covers: AC-15
@pytest.mark.parametrize(
    ("text", "ids"), [("", ()), ("abc123\n", ("abc123",)), ("abc\ndef\n", ("abc", "def"))]
)
def test_container_ids_are_parsed_from_docker_ps(text: str, ids: tuple[str, ...]) -> None:
    assert parse_container_ids(text) == ids


# covers: AC-16
@pytest.mark.parametrize(
    ("header", "reason"),
    [
        (replace(HEADER, entries=HEADER.entries[:1]), "missing Beta"),
        (
            replace(
                HEADER, entries=(*HEADER.entries, HeaderEntry(DATABASE, "Gamma", "collection"))
            ),
            "unexpected Gamma",
        ),
        (
            replace(
                HEADER, entries=(HeaderEntry("JiraRepos", "Alpha", "collection"), HEADER.entries[1])
            ),
            "names database JiraRepos, JiraReposAnon, not JiraReposAnon",
        ),
        (replace(HEADER, server_version="6.0.3"), "major differs"),
    ],
)
def test_header_mismatches_block(header: DumpHeader, reason: str) -> None:
    assert reason in _blocks(replace(GOOD, header=Fact(value=header)))["header"]


# covers: AC-17
@pytest.mark.parametrize(
    ("change", "name"),
    [
        ({"tools_version": Fact(error="`docker exec` exited 127: not found")}, "mongorestore"),
        ({"container_login": Fact(error="`docker exec` exited 1: auth failed")}, "container login"),
        (
            {
                "server_version": Fact(error="login to Mongo from this machine failed"),
                "state": Fact(),
            },
            "host login",
        ),
    ],
)
def test_failed_logins_and_tools_block(change: dict[str, object], name: str) -> None:
    assert name in _blocks(replace(GOOD, **change))  # type: ignore[arg-type]


# covers: AC-18
def test_every_block_is_listed_once_and_missing_inputs_are_not_checked() -> None:
    facts = Facts(
        containers=Fact(error="docker is not installed or not on PATH"),
        server_version=Fact(error="login to Mongo from this machine failed: refused"),
        receipt=Fact(error="no dataset.receipt in /d (run scripts/fetch_data.sh)"),
        other_containers=Fact(),
        memory=Fact(),
    )
    results = evaluate(facts, False, EXPECTED, LIMITS)
    blocks = [result.name for result in results if result.status == "BLOCK"]
    assert blocks == ["container", "host login", "receipt"]
    by_name = {result.name: result for result in results}
    assert by_name["state"].value == "not checked (needs host login)"
    assert by_name["zip"].value == "not checked (needs receipt)"
    assert by_name["disk"].value == "not checked (needs container)"
    assert by_name["header"].value == "not checked (needs zip)"
    message = blocking_message(results)
    assert message is not None
    assert message.startswith("restore blocked by 3 check(s), nothing was written:\n")
    assert message.count("\n") == 3


# covers: AC-19
@pytest.mark.parametrize(
    ("change", "name"),
    [
        ({"other_containers": Fact(value=("tracepath-neo4j-1",))}, "other containers"),
        ({"memory": Fact(value=3 * GB)}, "memory"),
        ({"server_version": Fact(value="7.1.0")}, "server minor"),
        ({"tools_version": Fact(value="101.0.0")}, "tools major"),
    ],
)
def test_warnings_let_the_run_continue(change: dict[str, object], name: str) -> None:
    results = _results(replace(GOOD, **change))  # type: ignore[arg-type]
    assert results[name].status == "WARN"
    assert blocking_message(tuple(results.values())) is None


def test_a_patch_gap_is_silent() -> None:
    assert _results(GOOD)["server minor"].status == "PASS"


# covers: AC-20, AC-22
@pytest.mark.parametrize("state", [State.PARTIAL, State.DRIFT])
def test_data_without_a_valid_marker_blocks_unless_reset(state: State) -> None:
    facts = replace(GOOD, state=Fact(value=state))
    assert "rerun with --reset" in _blocks(facts)["state"]
    assert _blocks(facts, reset=True) == {}


# covers: AC-21
def test_complete_skips_the_restore_only_checks_and_still_checks_the_tools() -> None:
    results = _results(COMPLETE)
    for name in ("receipt", "zip", "disk", "header"):
        assert results[name].status == "SKIP"
        assert results[name].value == "not needed, already restored"
    assert results["state"].value.endswith("a plain run skips")
    assert results["mongorestore"].status == "PASS"
    # The versions come from the marker when the header is not read.
    assert results["tools major"].status == "PASS"
    assert blocking_message(tuple(results.values())) is None
    blocked = _results(replace(COMPLETE, tools_version=Fact(error="exited 127")))
    assert blocked["mongorestore"].status == "BLOCK"


# covers: AC-23
def test_complete_with_reset_runs_every_restore_check() -> None:
    results = _results(COMPLETE, reset=True)
    assert results["receipt"].status == "BLOCK"
    assert results["state"].value.endswith("--reset restores it again")


@pytest.mark.parametrize(
    ("marker", "live", "state"),
    [
        (None, {}, State.ABSENT),
        (None, {"Alpha": 1}, State.PARTIAL),
        (EXPECTED, dict(EXPECTED), State.COMPLETE),
        (EXPECTED, {"Alpha": 3}, State.DRIFT),
        (EXPECTED, {}, State.DRIFT),
    ],
)
def test_state_is_worked_out_from_marker_and_live(
    marker: dict[str, int] | None, live: dict[str, int], state: State
) -> None:
    assert work_out_state(marker, live) is state


def test_docker_output_parsers() -> None:
    assert parse_other_containers("orgos-mongo-1\torgos\nother-1\tother\nloose\t\n") == (
        "other-1",
        "loose",
    )
    df = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/vda1 100 40 60 40% /data/db\n"
    )
    assert parse_df_free_bytes(df) == 60 * 1024
    with pytest.raises(ValueError, match="unexpected df output"):
        parse_df_free_bytes("nothing")
    assert parse_tools_version("mongorestore version: 100.19.1\ngit version: abc\n") == "100.19.1"
    with pytest.raises(ValueError, match="no version"):
        parse_tools_version("command not found")


def test_lines_are_logged_with_tabs_at_their_levels(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    results = evaluate(
        replace(GOOD, memory=Fact(value=1), zip=Fact(error="gone")), False, EXPECTED, LIMITS
    )
    log_results(results)
    levels = {record.getMessage().split("\t")[1]: record.levelname for record in caplog.records}
    assert levels["memory"] == "WARNING"
    assert levels["zip"] == "ERROR"
    assert levels["container"] == "INFO"
    assert "PASS\tcontainer\tabc123" in caplog.messages
