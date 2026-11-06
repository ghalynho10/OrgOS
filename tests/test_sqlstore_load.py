"""Fast checks of the SQL load's pure parts: figures, consistency, dbt results, record, CLI.

Spec 0009 AC-13, AC-15 to AC-20. No database: the shell runs on the full
corpus (the gate) and in the fixture tests.
"""

import json
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest

from orgos.cli import EXIT_CODES, run
from orgos.config import Settings, load_settings
from orgos.ingest import IngestError
from orgos.sqlstore import SqlStoreError
from orgos.sqlstore.dbt import RUN_RESULTS as RESULTS_FILE
from orgos.sqlstore.dbt import (
    Failure,
    failure_report,
    failures,
    format_failures,
    read_run_results,
    results_summary,
    run_dbt,
)
from orgos.sqlstore.figures import (
    CORPUS_FILE,
    EXPECTED_TABLES,
    KNOWN_ANSWERS_RECORD,
    NET_CHECK_SET,
    Consistency,
    Figure,
    TableLoad,
    collapsed_record,
    compare_figures,
    consistency_problems,
    expected_figures,
    gate_message,
    missing_tables,
)
from orgos.sqlstore.load import _read_json, _StepFailed, summary
from orgos.sqlstore.record import RunState, build_record

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / CORPUS_FILE).read_text(encoding="utf-8"))
KNOWN = json.loads((ROOT / KNOWN_ANSWERS_RECORD).read_text(encoding="utf-8"))


# covers: AC-15 (a figure that differs fails; every difference listed together)
def test_compare_figures_marks_each_figure() -> None:
    expected = (Figure("a", 1, "spec"), Figure("b", {"X": 2}, "file"), Figure("c", 0, "spec"))
    results = compare_figures(expected, {"a": 1, "b": {"X": 3}})
    assert [(r.name, r.found, r.ok) for r in results] == [
        ("a", 1, True),
        ("b", {"X": 3}, False),
        ("c", None, False),
    ]
    message = gate_message(results)
    assert message is not None
    assert message.startswith("figure gate failed: 2 figure(s) differ")
    assert "b: expected {'X': 2}, found {'X': 3} (file)" in message
    assert "c: expected 0, found None (spec)" in message


def test_gate_message_is_none_when_every_figure_matches() -> None:
    assert gate_message(compare_figures((Figure("a", 1, "spec"),), {"a": 1})) is None


# covers: AC-16 (file values are read, not typed in)
def test_expected_figures_read_the_two_files() -> None:
    figures = {figure.name: figure for figure in expected_figures(CORPUS, KNOWN)}
    seconds = figures["positive seconds per project"]
    assert seconds.expected == {p["key"]: p["positive_seconds"] for p in CORPUS["projects"]}
    assert seconds.source == f"file: {CORPUS_FILE}"
    people = figures["distinct persons per project"].expected
    assert people == {p["key"]: p["people"] for p in CORPUS["projects"]}
    assert figures["collapsed keys"].expected == collapsed_record(KNOWN["collapsed_documents"])
    assert list(figures["collapsed keys"].expected) == [
        "BAM-11750",
        "CWD-203",
        "JRASERVER-15889",
        "JRASERVER-40042",
    ]
    assert figures["net check set"].expected == list(NET_CHECK_SET) == ["FE-4996"]
    assert figures["flagged accounts"].expected == []


def test_collapsed_record_sorts_keys_and_dropped_ids() -> None:
    collapsed = {
        "B-1": {"kept": "a1", "dropped": ["a3", "a2"]},
        "A-1": {"kept": "b", "dropped": []},
    }
    assert collapsed_record(collapsed) == {
        "A-1": {"kept": "b", "dropped": []},
        "B-1": {"kept": "a1", "dropped": ["a2", "a3"]},
    }


# covers: AC-17 (one load ID everywhere, equal to the latest; an empty table fails)
def test_consistency_passes_with_one_load_everywhere() -> None:
    tables = [TableLoad("raw.jira_issue", 5, ("L2",)), TableLoad("model_int.issue", 4, ("L2",))]
    assert consistency_problems("L2", tables) == ()


def test_consistency_names_each_table_that_differs() -> None:
    tables = [
        TableLoad("raw.jira_issue", 5, ("L2",)),
        TableLoad("model.person", 3, ("L1",)),
        TableLoad("model.issue", 4, ("L1", "L2")),
        TableLoad("model_int.fixed_in", 0, ()),
    ]
    assert consistency_problems("L2", tables) == (
        "model.person holds load IDs L1, expected L2",
        "model.issue holds load IDs L1, L2, expected L2",
        "model_int.fixed_in is empty",
    )


def test_consistency_fails_with_no_completed_load() -> None:
    assert consistency_problems(None, []) == ("raw._dlt_loads has no completed load",)


# covers: AC-17 (a table dbt never built is named, not silently skipped)
def test_expected_tables_are_the_12_model_int_and_7_model_tables() -> None:
    schemas = [schema for schema, _ in EXPECTED_TABLES]
    assert (schemas.count("model_int"), schemas.count("model")) == (12, 7)
    assert len(set(EXPECTED_TABLES)) == 19


def test_missing_tables_is_empty_when_every_table_was_built() -> None:
    assert missing_tables(EXPECTED_TABLES) == ()


def test_missing_tables_names_each_absent_table_in_order() -> None:
    absent = {("model_int", "has_parent"), ("model", "issue")}
    built = [table for table in EXPECTED_TABLES if table not in absent]
    assert missing_tables(built) == (
        "model_int.has_parent does not exist",
        "model.issue does not exist",
    )


def test_missing_tables_names_all_19_on_an_empty_database() -> None:
    problems = missing_tables([])
    assert len(problems) == 19
    assert problems[0] == "model_int.issue does not exist"
    assert problems[-1] == "model.logged_time does not exist"


def test_missing_tables_ignores_extra_tables_and_tells_schemas_apart() -> None:
    # model.has_parent is not model_int.has_parent; an unexpected table is no problem.
    built = [t for t in EXPECTED_TABLES if t != ("model_int", "has_parent")]
    built += [("model", "has_parent"), ("model", "scratch")]
    assert missing_tables(built) == ("model_int.has_parent does not exist",)


RUN_RESULTS = {
    "results": [
        {"unique_id": "model.orgos.int_issue", "status": "success", "message": "INSERT 0 3"},
        {
            "unique_id": "test.orgos.stg_link_views_disagree",
            "status": "fail",
            "message": "Got 1 result, configured to fail if != 0",
            "relation_name": '"orgos"."test_failures"."stg_link_views_disagree"',
        },
        {
            "unique_id": "model.orgos.int_person",
            "status": "error",
            "message": "duplicate key value violates unique constraint\n"
            "Key (person_key)=(P) exists.",
            "relation_name": '"orgos"."model_int"."person"',
        },
        {"unique_id": "model.orgos.issue", "status": "skipped", "message": None},
        {"unique_id": "unit_test.orgos.int_issue.utc", "status": "pass", "message": None},
    ]
}


# covers: AC-13 (failing tests give their relation; failed models give their message)
def test_failures_reads_failed_tests_and_models() -> None:
    assert failures(RUN_RESULTS) == (
        Failure(
            "test.orgos.stg_link_views_disagree",
            "fail",
            "Got 1 result, configured to fail if != 0",
            '"orgos"."test_failures"."stg_link_views_disagree"',
        ),
        Failure(
            "model.orgos.int_person",
            "error",
            "duplicate key value violates unique constraint\nKey (person_key)=(P) exists.",
            None,
        ),
    )


def test_format_failures_prints_stored_rows_under_the_test() -> None:
    found = failures(RUN_RESULTS)
    rows = {
        '"orgos"."test_failures"."stg_link_views_disagree"': (
            ("link_id", "views", "versions"),
            [("12345", 2, 2)],
        )
    }
    report = format_failures(found, rows)
    assert "FAIL test.orgos.stg_link_views_disagree" in report
    assert "  link_id | views | versions\n  12345 | 2 | 2" in report
    assert "ERROR model.orgos.int_person" in report
    assert "Key (person_key)=(P) exists." in report


def test_results_summary_counts_status_per_resource_type() -> None:
    assert results_summary(RUN_RESULTS) == {
        "model": {"error": 1, "skipped": 1, "success": 1},
        "test": {"fail": 1},
        "unit_test": {"pass": 1},
    }
    assert results_summary(None) == {}


# covers: AC-13 (a missing results file means dbt wrote none)
def test_read_run_results_is_none_when_dbt_wrote_no_file(tmp_path: Path) -> None:
    assert read_run_results(tmp_path) is None


def test_read_run_results_parses_the_file(tmp_path: Path) -> None:
    (tmp_path / RESULTS_FILE).parent.mkdir()
    (tmp_path / RESULTS_FILE).write_text(json.dumps(RUN_RESULTS), encoding="utf-8")
    assert read_run_results(tmp_path) == RUN_RESULTS


def test_read_run_results_names_a_truncated_file(tmp_path: Path) -> None:
    (tmp_path / RESULTS_FILE).parent.mkdir()
    (tmp_path / RESULTS_FILE).write_text('{"results": [', encoding="utf-8")
    with pytest.raises(SqlStoreError, match=r"run_results\.json is not valid JSON"):
        read_run_results(tmp_path)


def test_read_run_results_names_an_unreadable_file(tmp_path: Path) -> None:
    # A directory where the file should be: it exists but cannot be read as text.
    (tmp_path / RESULTS_FILE).mkdir(parents=True)
    with pytest.raises(SqlStoreError, match=r"Cannot read .*run_results\.json"):
        read_run_results(tmp_path)


# covers: AC-13 as amended (no results file: dbt's exit code, and where to look next)
@pytest.mark.parametrize("exit_code", [2, -2, 137])
def test_failure_report_without_results_carries_the_exit_code(exit_code: int) -> None:
    # The no results branch reads no stored rows, so it needs no connection.
    report = failure_report(None, exit_code, cast("psycopg.Connection[Any]", None))
    assert f"exit code {exit_code}," in report
    assert "dbt stopped before finishing" in report
    assert "no target/run_results.json" in report
    assert "may have built some tables" in report
    assert "load_consistency.problems in results/sql_load/run_record.json" in report
    assert "before running" not in report


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    return load_settings(
        _env_file=None,
        POSTGRES_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_OWNER_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_READER_PASSWORD=secrets.token_hex(8),
        MONGO_INITDB_ROOT_PASSWORD=secrets.token_hex(8),
        ORGOS_MONGO_READER_PASSWORD=secrets.token_hex(8),
        NEO4J_AUTH=f"neo4j/{secrets.token_hex(8)}",
    )


# covers: AC-13 (an earlier run's file is deleted before dbt starts; the exit code comes back)
def test_run_dbt_deletes_the_old_results_before_dbt_starts(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / RESULTS_FILE
    old.parent.mkdir()
    old.write_text(json.dumps(RUN_RESULTS), encoding="utf-8")
    seen: list[bool] = []

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        seen.append(old.exists())
        return subprocess.CompletedProcess(command, returncode=137)

    monkeypatch.setattr("orgos.sqlstore.dbt.subprocess.run", fake_run)

    exit_code = run_dbt(settings, tmp_path)

    assert seen == [False]
    assert exit_code == 137
    assert read_run_results(tmp_path) is None


def test_run_dbt_starts_with_no_old_results(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "orgos.sqlstore.dbt.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(command, returncode=0),
    )
    assert run_dbt(settings, tmp_path) == 0


GIT = {"commit": "abc123", "dirty": False}
VERSIONS = {"python": "3.12.0", "dlt": "1.31.0"}
TIME = datetime(2026, 11, 5, 1, 2, 3, 456789, tzinfo=UTC)


# covers: AC-18 (ok only when nothing failed; a failure names its step)
def test_record_status_follows_the_failed_step() -> None:
    ok = build_record(RunState(), failed_step=None, time=TIME, git=GIT, versions=VERSIONS)
    assert (ok["status"], ok["failed_step"], ok["time"]) == ("ok", None, "2026-11-05T01:02:03Z")
    failed = build_record(RunState(), failed_step="figures", time=TIME, git=GIT, versions=VERSIONS)
    assert (failed["status"], failed["failed_step"]) == ("fail", "figures")
    with pytest.raises(ValueError, match="unknown step"):
        build_record(RunState(), failed_step="preflight", time=TIME, git=GIT, versions=VERSIONS)


def test_record_holds_the_spec_fields_and_no_login() -> None:
    state = RunState(
        dlt_load_id="L2",
        row_counts={"raw.jira_issue": 5},
        consistency=Consistency("L2", (TableLoad("raw.jira_issue", 5, ("L2",)),), ()),
        net_check=("FE-4996",),
    )
    record = build_record(state, failed_step=None, time=TIME, git=GIT, versions=VERSIONS)
    assert set(record) == {
        "time",
        "status",
        "failed_step",
        "git",
        "dlt_load_id",
        "versions",
        "row_counts",
        "figures",
        "collapsed_keys",
        "dropped_links_per_project",
        "net_check_set",
        "load_consistency",
        "dbt_tests",
    }
    assert record["load_consistency"] == {
        "ok": True,
        "latest_load_id": "L2",
        "tables_checked": 1,
        "problems": [],
    }
    text = json.dumps(record)
    for login in Settings.model_fields:
        assert "password" not in text and "orgos_owner" not in text, login


def test_summary_lists_counts_and_figures() -> None:
    record = build_record(
        RunState(dlt_load_id="L2", row_counts={"raw.jira_issue": 87_839}),
        failed_step=None,
        time=TIME,
        git=GIT,
        versions=VERSIONS,
    )
    text = summary(record)
    assert text.splitlines()[0] == "orgos load sql: ok"
    assert "raw.jira_issue     87,839" in text
    assert "figures: 0 of 0 match" in text


# covers: AC-19 (a missing or unparseable file raises SqlStoreError)
def test_read_json_names_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SqlStoreError, match="Cannot read"):
        _read_json(tmp_path / "corpus.json", ())


def test_read_json_names_a_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SqlStoreError, match="is not valid JSON"):
        _read_json(path, ())


def test_read_json_names_a_missing_key(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text('{"reconciliation": {}}', encoding="utf-8")
    with pytest.raises(SqlStoreError, match=r"has no reconciliation\.entry mismatches"):
        _read_json(path, (("reconciliation", "entry mismatches", "expected"),))


# covers: AC-20 (database errors are named by the step that failed)
@pytest.mark.parametrize(
    ("step", "named"),
    [("dlt", IngestError), ("raw_count", IngestError), ("dbt", SqlStoreError)],
)
def test_step_failure_names_database_errors_by_step(step: str, named: type[Exception]) -> None:
    error = _StepFailed(step, RunState(), psycopg.OperationalError("connection lost")).named()
    assert isinstance(error, named)
    assert str(error) == f"{step} step failed: connection lost"


def test_step_failure_keeps_a_named_error() -> None:
    original = SqlStoreError("figure gate failed: 1 figure(s) differ")
    assert _StepFailed("figures", RunState(), original).named() is original


# covers: AC-20 (IngestError exits 5)
def test_exit_codes_hold_the_ingest_row() -> None:
    assert (IngestError, 5) in EXIT_CODES


# covers: AC-19 (help needs no .env)
def test_load_sql_help_needs_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        run(["load", "sql", "--help"])
    assert exit_info.value.code == 0
    assert "Load the 7 corpus projects into Postgres" in capsys.readouterr().out
