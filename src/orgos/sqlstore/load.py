"""`orgos load sql`: Mongo into `raw`, then `dbt build`, then the checks (spec 0009 AC-19).

The order is fixed: settings, preflight, dlt into raw, the raw count, `dbt build`
with every test, the load consistency check, the figure gate, the run record.
Preflight writes nothing; every failure after it writes the run record with
status `fail` and the step that failed, then raises. Progress goes to stderr as
log lines; the summary this returns goes to stdout.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from orgos.config import Settings
from orgos.ingest import CORPUS_PROJECTS, IngestError, check_raw_count, to_raw_row
from orgos.ingest.load import load_raw, raw_count, read_corpus
from orgos.mongostore import mongo_errors
from orgos.mongostore.connect import reader_client, secrets
from orgos.mongostore.dump import DATABASE
from orgos.sqlstore import SqlStoreError
from orgos.sqlstore.bootstrap import SCHEMAS
from orgos.sqlstore.dbt import failure_report, read_run_results, results_summary, run_dbt
from orgos.sqlstore.figures import (
    CORPUS_FILE,
    KNOWN_ANSWERS_RECORD,
    compare_figures,
    expected_figures,
    find_figures,
    gate_message,
    load_consistency,
)
from orgos.sqlstore.record import (
    RUN_RECORD,
    RunState,
    build_record,
    git_state,
    package_versions,
    write_run_record,
)

log = logging.getLogger(__name__)

DBT_DIR = Path("dbt")
PIPELINES_DIR = Path("data/dlt")
CONNECT_TIMEOUT_S = 10


@dataclass(frozen=True)
class Inputs:
    """The two committed files the gate reads, parsed by preflight."""

    corpus: Mapping[str, Any]
    known_answers: Mapping[str, Any]


def _read_json(path: Path, required: tuple[tuple[str, ...], ...]) -> Mapping[str, Any]:
    try:
        parsed: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SqlStoreError(f"Cannot read {path}: {error.strerror}") from None
    except json.JSONDecodeError as error:
        raise SqlStoreError(f"{path} is not valid JSON: {error}") from None
    for keys in required:
        value: Any = parsed
        for key in keys:
            if not isinstance(value, Mapping) or key not in value:
                raise SqlStoreError(f"{path} has no {'.'.join(keys)}")
            value = value[key]
    return parsed


def preflight(settings: Settings, root: Path) -> Inputs:
    """Check the owner login, the five schemas, the Mongo reader and both files; write nothing."""
    try:
        with psycopg.connect(
            settings.pg_conninfo("owner"), connect_timeout=CONNECT_TIMEOUT_S
        ) as conn:
            rows = conn.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)", (list(SCHEMAS),)
            ).fetchall()
    except psycopg.OperationalError as error:
        raise SqlStoreError(
            f"Could not reach Postgres at {settings.pg_host}:{settings.pg_port}: {error}"
        ) from None
    missing = [schema for schema in SCHEMAS if (schema,) not in rows]
    if missing:
        raise SqlStoreError(
            f"Postgres has no schema {', '.join(missing)}; run `uv run orgos bootstrap` first"
        )

    with (
        mongo_errors("Connecting to Mongo as the reader", secrets(settings)),
        reader_client(settings) as client,
    ):
        client[DATABASE].command("ping")

    corpus = _read_json(root / CORPUS_FILE, (("projects",),))
    known_answers = _read_json(
        root / KNOWN_ANSWERS_RECORD,
        (
            ("collapsed_documents",),
            ("flagged_accounts",),
            ("reconciliation", "entry mismatches", "expected"),
        ),
    )
    log.info("preflight passed")
    return Inputs(corpus=corpus, known_answers=known_answers)


def _write(root: Path, state: RunState, failed_step: str | None) -> dict[str, Any]:
    record = build_record(
        state,
        failed_step=failed_step,
        time=datetime.now(UTC),
        git=git_state(root),
        versions=package_versions(),
    )
    write_run_record(root / RUN_RECORD, record)
    log.info("wrote %s with status %s", RUN_RECORD, record["status"])
    return record


class _StepFailed(Exception):
    # A step's error with the step and what the run had learned before it.
    def __init__(self, step: str, state: RunState, error: Exception) -> None:
        super().__init__(str(error))
        self.step = step
        self.state = state
        self.error = error

    def named(self) -> IngestError | SqlStoreError:
        """The error to raise: the step's own named error, or a database error named by step."""
        if isinstance(self.error, IngestError | SqlStoreError):
            return self.error
        named = IngestError if self.step in ("dlt", "raw_count") else SqlStoreError
        return named(f"{self.step} step failed: {self.error}")


def _run(settings: Settings, root: Path, inputs: Inputs) -> RunState:
    owner = settings.pg_conninfo("owner")
    step, state = "dlt", RunState()
    try:
        with reader_client(settings) as client:
            read = read_corpus(client, CORPUS_PROJECTS)
            rows = (to_raw_row(doc) for doc in read.documents)
            load_id = load_raw(settings.pg_owner_url, rows, root / PIPELINES_DIR)
        state = replace(state, dlt_load_id=load_id)

        step = "raw_count"
        check_raw_count(read.count, raw_count(owner))
        log.info("raw.jira_issue holds all %s documents", f"{read.count:,}")

        step = "dbt"
        passed = run_dbt(settings, root / DBT_DIR)
        run_results = read_run_results(root / DBT_DIR)
        with psycopg.connect(owner) as conn:
            # Checked whether dbt passed or not (AC-17), so the record shows it.
            consistency = load_consistency(conn)
            state = replace(
                state,
                dbt_summary=results_summary(run_results),
                consistency=consistency,
                row_counts={table.table: table.rows for table in consistency.tables},
            )
            if not passed:
                raise SqlStoreError("dbt build failed:\n" + failure_report(run_results, conn))

            step = "load_consistency"
            if not consistency.ok:
                raise SqlStoreError(
                    "load consistency check failed:\n  " + "\n  ".join(consistency.problems)
                )
            log.info("load consistency passed: one load ID %s", consistency.latest_load_id)

            step = "figures"
            found = find_figures(conn, inputs.known_answers)

        results = compare_figures(
            expected_figures(inputs.corpus, inputs.known_answers), found.values
        )
        state = replace(
            state,
            figures=results,
            collapsed=found.collapsed,
            dropped_links_per_project=found.dropped_links_per_project,
            net_check=found.net_check,
        )
        message = gate_message(results)
        if message is not None:
            raise SqlStoreError(message)
    except (IngestError, SqlStoreError, psycopg.Error) as error:
        raise _StepFailed(step, state, error) from error
    log.info("figure gate passed: %s figures match", len(results))
    return state


def load_sql(settings: Settings, root: Path) -> str:
    """Load the corpus and build both SQL layers; return the stdout summary.

    Raises `IngestError` for the Mongo read, the raw shape, the dlt load and
    the raw count, and `SqlStoreError` for preflight, dbt, the load
    consistency check and the figure gate. After preflight the run record is
    written either way.
    """
    inputs = preflight(settings, root)
    try:
        state = _run(settings, root, inputs)
    except _StepFailed as failed:
        _write(root, failed.state, failed.step)
        raise failed.named() from None
    return summary(_write(root, state, None))


def summary(record: Mapping[str, Any]) -> str:
    """The plain stdout summary: status, load ID, row counts and figures."""
    figures = record["figures"]
    matched = sum(1 for figure in figures if figure["ok"])
    width = max((len(table) for table in record["row_counts"]), default=0)
    lines = [f"orgos load sql: {record['status']}", f"dlt load {record['dlt_load_id']}"]
    lines.extend(f"  {table:<{width}}  {rows:>9,}" for table, rows in record["row_counts"].items())
    lines.append(f"figures: {matched} of {len(figures)} match")
    lines.append(f"run record: {RUN_RECORD}")
    return "\n".join(lines)
