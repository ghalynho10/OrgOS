"""Fast checks of the graph load's pure checks, its run record, its flow and its CLI.

Spec 0010 AC-1, AC-2, AC-6, AC-8, AC-10, AC-15, AC-20, AC-21 and AC-23. No
database: the flow tests replace every store call with a fake, and the real
stores are covered by the integration tests and the full run.
"""

import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

from orgos.cli import EXIT_CODES, run
from orgos.config import Settings, load_settings
from orgos.graphstore import GraphStoreError
from orgos.graphstore import load as graph_load
from orgos.graphstore.checks import (
    FoundCounts,
    GraphState,
    check_run_records,
    expected_counts,
    graph_schema_problems,
    graph_state_problems,
    reconcile,
)
from orgos.graphstore.compare import Comparison, comparison
from orgos.graphstore.load import (
    Inputs,
    ModelInt,
    _StepFailed,
    consistency_problems,
    load_graph,
    summary,
)
from orgos.graphstore.mapping import load_mapping
from orgos.graphstore.record import RUN_RECORD, RunState, build_record
from orgos.sqlstore.figures import Consistency, TableLoad

MAPPING = load_mapping()
SQL = "results/sql_load/run_record.json"
GRAPH = "results/graph_load/run_record.json"


# covers: AC-20 (the run records agree and say ok; each problem names its file)
def test_run_records_pass_when_both_say_ok_with_one_load_id() -> None:
    sql = (SQL, {"status": "ok", "dlt_load_id": "L1"})
    assert check_run_records(sql, None) == ()
    assert check_run_records(sql, (GRAPH, {"status": "ok", "dlt_load_id": "L1"})) == ()


def test_run_records_name_a_failed_sql_record_and_a_missing_load_id() -> None:
    sql = (SQL, {"status": "fail", "failed_step": "figures", "dlt_load_id": None})
    assert check_run_records(sql, None) == (
        f"{SQL} says status 'fail', not 'ok' (failed step figures)",
        f"{SQL} holds no dlt_load_id",
    )


def test_run_records_name_a_failed_graph_record() -> None:
    sql = (SQL, {"status": "ok", "dlt_load_id": "L1"})
    graph = (GRAPH, {"status": "fail", "failed_step": "nodes", "dlt_load_id": "L1"})
    assert check_run_records(sql, graph) == (
        f"{GRAPH} says status 'fail', not 'ok' (failed step nodes)",
    )


def test_run_records_name_two_load_ids() -> None:
    sql = (SQL, {"status": "ok", "dlt_load_id": "L2"})
    graph = (GRAPH, {"status": "ok", "dlt_load_id": "L1"})
    assert check_run_records(sql, graph) == (f"{GRAPH} holds dlt_load_id L1, but {SQL} holds L2",)


# The SHOW output after a good load, as the trial and the probe saw it.
CONSTRAINTS = tuple(
    {
        "name": node.constraint,
        "type": "NODE_PROPERTY_UNIQUENESS",
        "entityType": "NODE",
        "labelsOrTypes": [node.label],
        "properties": [node.key.property],
    }
    for node in MAPPING.nodes
)
INDEXES = (
    *(
        {
            "name": node.constraint,
            "type": "RANGE",
            "entityType": "NODE",
            "labelsOrTypes": [node.label],
            "properties": [node.key.property],
            "owningConstraint": node.constraint,
        }
        for node in MAPPING.nodes
    ),
    {"name": "index_460996c0", "type": "LOOKUP", "entityType": "NODE"},
    {"name": "index_1b9dcc97", "type": "LOOKUP", "entityType": "RELATIONSHIP"},
)


def _state(**changes: Any) -> GraphState:
    values: dict[str, Any] = {
        "constraints": CONSTRAINTS,
        "indexes": INDEXES,
        "node_load_ids": ("L1",),
        "relationship_load_ids": ("L1",),
        "access": "read-only",
    }
    return GraphState(**(values | changes))


# covers: AC-10, AC-17 (exactly the four constraints and six indexes, compared on what they are)
def test_schema_passes_the_expected_constraints_and_indexes() -> None:
    assert graph_schema_problems(_state(), MAPPING) == ()


def test_schema_ignores_generated_names() -> None:
    renamed = tuple({**index, "name": f"other_{n}"} for n, index in enumerate(INDEXES))
    assert graph_schema_problems(_state(indexes=renamed), MAPPING) == ()


def test_schema_names_a_stray_index() -> None:
    stray = {
        "name": "issue_status",
        "type": "RANGE",
        "entityType": "NODE",
        "labelsOrTypes": ["Issue"],
        "properties": ["statusName"],
        "owningConstraint": None,
    }
    assert graph_schema_problems(_state(indexes=(*INDEXES, stray)), MAPPING) == (
        "unexpected index issue_status: RANGE on NODE Issue(statusName)",
    )


def test_schema_names_a_missing_constraint_and_its_index() -> None:
    state = _state(constraints=CONSTRAINTS[1:], indexes=INDEXES[1:])
    assert graph_schema_problems(state, MAPPING) == (
        "missing constraint: NODE_PROPERTY_UNIQUENESS on NODE Issue(issueKey)",
        "missing index: RANGE on NODE Issue(issueKey) owned by a constraint",
    )


def test_schema_names_an_index_with_the_right_shape_but_no_constraint() -> None:
    loose = {**INDEXES[0], "name": "loose", "owningConstraint": None}
    state = _state(constraints=CONSTRAINTS[1:], indexes=(loose, *INDEXES[1:]))
    assert graph_schema_problems(state, MAPPING) == (
        "missing constraint: NODE_PROPERTY_UNIQUENESS on NODE Issue(issueKey)",
        "missing index: RANGE on NODE Issue(issueKey) owned by a constraint",
        "unexpected index loose: RANGE on NODE Issue(issueKey)",
    )


# covers: AC-17 (one loadId equal to the load's, the database read only, the schema too)
def test_state_passes_one_load_id_read_only() -> None:
    assert graph_state_problems(_state(), MAPPING, "L1") == ()


def test_state_names_a_second_load_id_and_a_null_one() -> None:
    state = _state(node_load_ids=("L1", None), relationship_load_ids=("L0",))
    assert graph_state_problems(state, MAPPING, "L1") == (
        "the graph holds loadId L0, L1, null, expected only L1",
    )


def test_state_names_an_empty_graph() -> None:
    state = _state(node_load_ids=(), relationship_load_ids=())
    assert graph_state_problems(state, MAPPING, "L1") == (
        "the graph holds loadId none, expected only L1",
    )


def test_state_names_a_load_id_other_than_the_load() -> None:
    assert graph_state_problems(_state(), MAPPING, "L2") == (
        "the graph holds loadId L1, expected only L2",
    )


def test_state_names_a_writable_database() -> None:
    assert graph_state_problems(_state(access="read-write"), MAPPING, "L1") == (
        "the database neo4j is read-write, not read-only",
    )


def test_state_includes_the_schema_problems() -> None:
    state = _state(constraints=CONSTRAINTS[1:])
    assert graph_state_problems(state, MAPPING, "L1") == (
        "missing constraint: NODE_PROPERTY_UNIQUENESS on NODE Issue(issueKey)",
    )


# The SQL record's counts of the corpus (results/sql_load/run_record.json).
TABLE_ROWS = {
    "issue": 87_835,
    "project": 7,
    "person": 16_942,
    "fix_version": 1_846,
    "in_project": 87_835,
    "assigned_to": 24_659,
    "reported_by": 86_895,
    "created_by": 87_501,
    "has_parent": 1_181,
    "fixed_in": 40_833,
    "issue_link": 21_721,
    "logged_time": 7_995,
}
TYPE_ROWS = {"Relate": 20_000, "Bonfire Testing": 1_721}


def _good_counts() -> tuple[dict[str, int], FoundCounts]:
    expected = expected_counts(MAPPING, TABLE_ROWS, TYPE_ROWS)
    labels = {node.label: (expected[node.label],) * 2 for node in MAPPING.nodes}
    types = {kind: (expected[kind],) * 2 for kind in MAPPING.relationship_types if expected[kind]}
    found = FoundCounts(
        labels=labels,
        types=types,
        nodes=sum(count for count, _ in labels.values()),
        relationships=sum(count for count, _ in types.values()),
    )
    return expected, found


# covers: AC-15 (every label and all 24 types, a type with no rows at 0, the totals)
def test_expected_counts_cover_every_label_and_all_24_types() -> None:
    expected = expected_counts(MAPPING, TABLE_ROWS, TYPE_ROWS)
    assert len(expected) == 4 + 24
    assert (expected["RELATE"], expected["BONFIRE_TESTING"], expected["BLOCKER"]) == (
        20_000,
        1_721,
        0,
    )
    assert expected["LOGGED_TIME"] == 7_995


def test_expected_counts_refuse_an_unlisted_edge_type() -> None:
    with pytest.raises(GraphStoreError, match="outside the mapping: Blocks"):
        expected_counts(MAPPING, TABLE_ROWS, {"Blocks": 1})


def test_reconciliation_passes_when_every_count_agrees() -> None:
    expected, found = _good_counts()
    result = reconcile(MAPPING, expected, found)
    assert result.problems == ()
    assert result.nodes == 106_630
    assert result.relationships == 358_620
    assert result.types["BLOCKER"].record() == {"expected": 0, "found": 0, "distinct": 0}


def test_reconciliation_names_one_edge_id_with_two_relationships() -> None:
    expected, found = _good_counts()
    types = {**found.types, "HAS_PARENT": (1_182, 1_181)}
    found = FoundCounts(found.labels, types, found.nodes, found.relationships + 1)
    assert reconcile(MAPPING, expected, found).problems == (
        "HAS_PARENT: 1,181 model_int rows, 1,182 found, 1,181 distinct",
    )


def test_reconciliation_names_a_missing_link_type() -> None:
    expected, found = _good_counts()
    types = {kind: counts for kind, counts in found.types.items() if kind != "RELATE"}
    found = FoundCounts(found.labels, types, found.nodes, found.relationships - 20_000)
    assert reconcile(MAPPING, expected, found).problems == (
        "RELATE: 20,000 model_int rows, 0 found, 0 distinct",
    )


def test_reconciliation_names_a_type_outside_the_mapping() -> None:
    expected, found = _good_counts()
    types = {**found.types, "LINKED": (3, 3)}
    found = FoundCounts(found.labels, types, found.nodes, found.relationships + 3)
    assert reconcile(MAPPING, expected, found).problems == (
        "relationship type LINKED is not in the mapping (3 found)",
        "358,623 relationships in all, but the 24 types add up to 358,620",
    )


def test_reconciliation_names_a_node_outside_the_four_labels() -> None:
    expected, found = _good_counts()
    found = FoundCounts(found.labels, found.types, found.nodes + 1, found.relationships)
    assert reconcile(MAPPING, expected, found).problems == (
        "106,631 nodes in all, but the four labels add up to 106,630",
    )


GIT = {"commit": "abc123", "dirty": False}
VERSIONS = {"python": "3.12.0", "neo4j": "6.4.1", "psycopg": "3.3.0"}
TIME = datetime(2026, 11, 7, 1, 2, 3, 456789, tzinfo=UTC)


# covers: AC-6, AC-21 (ok only when nothing failed; the spec fields; no login)
def test_record_status_follows_the_failed_step() -> None:
    started = build_record(
        RunState("L1"), failed_step="started", time=TIME, git=GIT, versions=VERSIONS
    )
    assert (started["status"], started["failed_step"]) == ("fail", "started")
    ok = build_record(RunState("L1"), failed_step=None, time=TIME, git=GIT, versions=VERSIONS)
    assert (ok["status"], ok["time"]) == ("ok", "2026-11-07T01:02:03Z")
    with pytest.raises(ValueError, match="unknown step"):
        build_record(RunState("L1"), failed_step="preflight", time=TIME, git=GIT, versions=VERSIONS)


def test_record_holds_the_spec_fields_and_no_login() -> None:
    expected, found = _good_counts()
    state = RunState(
        dlt_load_id="L1",
        read_only_restored=True,
        neo4j_server="2026.10.1",
        constraints=CONSTRAINTS,
        indexes=INDEXES,
        reconciliation=reconcile(MAPPING, expected, found),
        checks={"temporal": (), "graph_state": (), "load_consistency": ()},
        comparison=comparison(106_630, 358_620, []),
        seconds={"nodes": 8.84, "relationships": 14.31},
    )
    record = build_record(state, failed_step=None, time=TIME, git=GIT, versions=VERSIONS)
    assert list(record) == [
        "time",
        "status",
        "failed_step",
        "git",
        "dlt_load_id",
        "versions",
        "read_only_restored",
        "show_constraints",
        "show_indexes",
        "reconciliation",
        "checks",
        "compare",
        "seconds",
    ]
    assert record["checks"] == {"temporal": [], "graph_state": [], "load_consistency": []}
    assert record["compare"] == {
        "nodes_compared": 106_630,
        "relationships_compared": 358_620,
        "differences": {},
    }
    assert record["versions"]["neo4j_server"] == "2026.10.1"
    assert record["reconciliation"]["labels"]["Issue"] == {
        "expected": 87_835,
        "found": 87_835,
        "distinct": 87_835,
    }
    assert record["seconds"] == {"nodes": 8.8, "relationships": 14.3}
    text = json.dumps(record)
    for secret_word in ("password", "neo4j/", "bolt://", "postgresql://", "orgos_owner"):
        assert secret_word not in text


def test_summary_lists_each_count_and_the_totals() -> None:
    expected, found = _good_counts()
    state = RunState("L1", reconciliation=reconcile(MAPPING, expected, found))
    text = summary(build_record(state, failed_step=None, time=TIME, git=GIT, versions=VERSIONS))
    lines = text.splitlines()
    assert lines[:2] == ["orgos load graph: ok", "dlt load L1"]
    assert "Issue" in lines[2] and "87,835" in lines[2]
    assert lines[-2:] == ["106,630 nodes, 358,620 relationships", f"run record: {RUN_RECORD}"]


# covers: AC-3, AC-19 (a latest load other than the SQL record's names both IDs and the tables)
def test_consistency_names_both_ids_and_the_tables() -> None:
    tables = (TableLoad("model_int.issue", 3, ("L2",)), TableLoad("model.issue", 3, ("L2",)))
    assert consistency_problems(Consistency("L1", tables, ()), "L1") == ()
    assert consistency_problems(Consistency("L2", tables, ()), "L1") == (
        "the latest load is L2, the SQL record holds L1;"
        " tables not holding L1: model_int.issue, model.issue",
    )
    stale = Consistency("L1", tables, ("model.issue holds L0",))
    assert consistency_problems(stale, "L1") == ("model.issue holds L0",)


# covers: AC-1 (every step failure, a database or subprocess error included, names its step)
def test_step_failure_names_the_step() -> None:
    assert str(_StepFailed("nodes", OSError("boom")).named()) == "nodes step failed: boom"
    named = _StepFailed("constraints", GraphStoreError("graph schema")).named()
    assert str(named) == "constraints step failed: graph schema"


def test_exit_codes_hold_the_graph_row() -> None:
    assert (GraphStoreError, 6) in EXIT_CODES


def test_load_graph_help_needs_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        run(["load", "graph", "--help"])
    assert exit_info.value.code == 0
    assert "Load model_int into Neo4j" in capsys.readouterr().out


# covers: AC-1, AC-2 (a SQL record that is missing, failed or not a JSON object is refused
# with exit 6 and one message, and nothing is written; preflight reads it before any store)
@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "run `uv run orgos load sql` first"),
        (
            json.dumps({"status": "fail", "failed_step": "figures", "dlt_load_id": "L1"}),
            f"The SQL load is not ready:\n  {SQL} says status 'fail', not 'ok'"
            " (failed step figures)",
        ),
        ('{"status": "ok",', "is not valid JSON"),
        ('["ok"]', "does not hold a JSON object"),
    ],
)
def test_load_graph_refuses_a_sql_record_that_is_not_ok_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str | None,
    message: str,
) -> None:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    env = {
        # Hosts that cannot resolve, so a store call made by mistake fails at once.
        "ORGOS_PG_HOST": "postgres.invalid",
        "ORGOS_NEO4J_HOST": "neo4j.invalid",
        "POSTGRES_PASSWORD": secrets.token_hex(8),
        "ORGOS_PG_OWNER_PASSWORD": secrets.token_hex(8),
        "ORGOS_PG_READER_PASSWORD": secrets.token_hex(8),
        "MONGO_INITDB_ROOT_PASSWORD": secrets.token_hex(8),
        "ORGOS_MONGO_READER_PASSWORD": secrets.token_hex(8),
        "NEO4J_AUTH": f"neo4j/{secrets.token_hex(8)}",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    if text is not None:
        (tmp_path / SQL).parent.mkdir(parents=True)
        (tmp_path / SQL).write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        run(["load", "graph"])
    assert exit_info.value.code == 6
    captured = capsys.readouterr()
    assert message in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert not (tmp_path / GRAPH).parent.exists()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    # Hosts that cannot resolve, so a store call the fakes miss fails at once.
    return load_settings(
        _env_file=None,
        ORGOS_PG_HOST="postgres.invalid",
        ORGOS_NEO4J_HOST="neo4j.invalid",
        POSTGRES_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_OWNER_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_READER_PASSWORD=secrets.token_hex(8),
        MONGO_INITDB_ROOT_PASSWORD=secrets.token_hex(8),
        ORGOS_MONGO_READER_PASSWORD=secrets.token_hex(8),
        NEO4J_AUTH=f"neo4j/{secrets.token_hex(8)}",
    )


class _FakeDriver:
    def __enter__(self) -> "_FakeDriver":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    failing_step: str | None,
    *,
    error: BaseException | None = None,
    restore_error: BaseException | None = None,
) -> list[str]:
    # Replace every store call; record the order of events and the record status at each.
    # `failing_step` raises `error` (a GraphStoreError by default); `restore_error`
    # is raised by the restart to read only.
    events: list[str] = []

    def status() -> str:
        return str(json.loads((root / RUN_RECORD).read_text())["status"])

    def fail(name: str) -> None:
        if name == failing_step:
            raise error if error is not None else GraphStoreError(f"{name} broke")

    def set_access(_: Settings, __: Path, *, read_only: bool) -> None:
        events.append(f"{'read only' if read_only else 'writable'} with record {status()}")
        if read_only and restore_error is not None:
            raise restore_error
        if not read_only:
            fail("writable")

    def step(name: str) -> Any:
        def write(*_: object) -> None:
            events.append(name)
            fail(name)

        return write

    monkeypatch.setattr(graph_load, "preflight", lambda *_: Inputs(load_id="L1", mapping=MAPPING))
    monkeypatch.setattr(
        graph_load, "read_model_int", lambda *_: ModelInt({}, TABLE_ROWS, TYPE_ROWS)
    )
    monkeypatch.setattr(graph_load, "set_access", set_access)
    monkeypatch.setattr(graph_load, "_driver", lambda _: _FakeDriver())
    monkeypatch.setattr(
        graph_load,
        "LOAD_STEPS",
        tuple((name, step(name)) for name in ("wipe", "constraints", "nodes", "relationships")),
    )
    expected, found = _good_counts()

    def check(_: Settings, __: Inputs, state: RunState, ___: ModelInt) -> Any:
        events.append(f"checks with record {status()}")
        return replace(state, reconciliation=reconcile(MAPPING, expected, found)), None

    monkeypatch.setattr(graph_load, "_check", check)

    def compare(*_: object) -> Comparison:
        events.append(f"compare with record {status()}")
        return comparison(106_630, 358_620, [])

    monkeypatch.setattr(graph_load, "compare_graph", compare)
    return events


# covers: AC-6 (fail before Neo4j is made writable, ok only as the last act), AC-8
def test_the_record_says_fail_until_the_very_end(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _fake_run(monkeypatch, tmp_path, None)
    text = load_graph(settings, tmp_path)
    assert events == [
        "writable with record fail",
        "wipe",
        "constraints",
        "nodes",
        "relationships",
        "read only with record fail",
        "checks with record fail",
        "compare with record fail",
    ]
    record = json.loads((tmp_path / RUN_RECORD).read_text())
    assert (record["status"], record["failed_step"], record["read_only_restored"]) == (
        "ok",
        None,
        True,
    )
    assert text.startswith("orgos load graph: ok")


def test_a_failed_step_still_makes_neo4j_read_only(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _fake_run(monkeypatch, tmp_path, "nodes")
    with pytest.raises(GraphStoreError, match=r"^nodes step failed: nodes broke$"):
        load_graph(settings, tmp_path)
    assert events == [
        "writable with record fail",
        "wipe",
        "constraints",
        "nodes",
        "read only with record fail",
    ]
    record = json.loads((tmp_path / RUN_RECORD).read_text())
    assert (record["status"], record["failed_step"], record["read_only_restored"]) == (
        "fail",
        "nodes",
        True,
    )


def _outcome(root: Path) -> tuple[str, str | None, bool | None]:
    record = json.loads((root / RUN_RECORD).read_text())
    return record["status"], record["failed_step"], record["read_only_restored"]


# covers: AC-6, AC-18 (a difference found by the compare fails the load; the record keeps it)
def test_a_compare_difference_fails_the_load(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, tmp_path, None)
    found = comparison(106_630, 358_620, [("node property", "Issue BAM-1 statusName")])
    monkeypatch.setattr(graph_load, "compare_graph", lambda *_: found)
    with pytest.raises(GraphStoreError) as raised:
        load_graph(settings, tmp_path)
    assert str(raised.value).splitlines() == [
        "compare step failed: The value level compare found differences:",
        "  node property: 1 (Issue BAM-1 statusName)",
    ]
    assert _outcome(tmp_path) == ("fail", "compare", True)
    record = json.loads((tmp_path / RUN_RECORD).read_text())
    assert record["compare"]["differences"] == {
        "node property": {"count": 1, "shown": ["Issue BAM-1 statusName"]}
    }


# covers: AC-8 (the restore runs after Ctrl+C, after a failed writable step; a failed
# or interrupted restore is reported), AC-6 (a failed read touches no Neo4j)
def test_ctrl_c_during_a_write_still_makes_neo4j_read_only(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _fake_run(monkeypatch, tmp_path, "relationships", error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        load_graph(settings, tmp_path)
    assert events[-2:] == ["relationships", "read only with record fail"]
    assert _outcome(tmp_path) == ("fail", "relationships", True)


def test_a_failed_writable_step_still_restores_read_only(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _fake_run(monkeypatch, tmp_path, "writable")
    with pytest.raises(GraphStoreError, match=r"^writable step failed: writable broke$"):
        load_graph(settings, tmp_path)
    assert events == ["writable with record fail", "read only with record fail"]
    assert _outcome(tmp_path) == ("fail", "writable", True)


def test_a_failed_restore_after_a_failed_step_names_both(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, tmp_path, "nodes", restore_error=OSError("compose broke"))
    with pytest.raises(GraphStoreError) as raised:
        load_graph(settings, tmp_path)
    assert str(raised.value).splitlines() == [
        "nodes step failed: nodes broke",
        "Neo4j may still be writable: the restart to read only failed"
        f" (read_only_restored is false in {RUN_RECORD}).",
    ]
    assert _outcome(tmp_path) == ("fail", "nodes", False)


def test_a_failed_record_write_after_a_failed_step_keeps_the_step_error(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _fake_run(monkeypatch, tmp_path, "nodes", restore_error=OSError("compose broke"))
    real_write = graph_load.write_run_record

    def write_once(path: Path, record: dict[str, Any]) -> None:
        # The started record lands; the record of the failed step does not.
        if record["failed_step"] != "started":
            raise OSError(28, "No space left on device")
        real_write(path, record)

    monkeypatch.setattr(graph_load, "write_run_record", write_once)
    with pytest.raises(GraphStoreError) as raised:
        load_graph(settings, tmp_path)
    assert str(raised.value).splitlines() == [
        "nodes step failed: nodes broke",
        "Neo4j may still be writable: the restart to read only failed"
        f" (read_only_restored is false in {RUN_RECORD}).",
    ]
    assert f"Cannot write {RUN_RECORD}: No space left on device" in caplog.text
    assert _outcome(tmp_path) == ("fail", "started", None)


def test_a_failed_restore_after_good_writes_fails_the_load(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _fake_run(monkeypatch, tmp_path, None, restore_error=OSError("compose broke"))
    with pytest.raises(GraphStoreError, match="Neo4j may still be writable"):
        load_graph(settings, tmp_path)
    assert not any(event.startswith("checks") for event in events)
    assert _outcome(tmp_path) == ("fail", "read_only", False)


def test_a_second_ctrl_c_during_the_restore_is_reported(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _fake_run(
        monkeypatch, tmp_path, "nodes", error=KeyboardInterrupt(), restore_error=KeyboardInterrupt()
    )
    with pytest.raises(KeyboardInterrupt):
        load_graph(settings, tmp_path)
    assert "Neo4j may still be writable" in caplog.text
    assert _outcome(tmp_path) == ("fail", "nodes", False)


def test_a_failed_read_before_writable_touches_no_neo4j(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _fake_run(monkeypatch, tmp_path, None)

    def down(*_: object) -> ModelInt:
        raise psycopg.OperationalError("server closed the connection")

    monkeypatch.setattr(graph_load, "read_model_int", down)
    with pytest.raises(GraphStoreError, match=r"^started step failed: server closed"):
        load_graph(settings, tmp_path)
    assert events == []
    assert _outcome(tmp_path) == ("fail", "started", None)
