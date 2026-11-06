"""Run `dbt build` as a subprocess and read what failed from its run results (spec 0009 AC-13).

`run_dbt` starts dbt from the running interpreter with the merged environment
of `orgos.config` (the owner login and `PGTZ`), after deleting the last
`target/run_results.json`, so a missing file afterwards means dbt failed before
running anything. The pure functions read that file: the failed nodes, and a
summary of every status. `failure_report` adds up to 20 stored rows of each
failing test, read as the owner, so the message names the link ID, issue key or
history ID.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from orgos.config import Settings, dbt_subprocess_env

log = logging.getLogger(__name__)

# dbt has no `python -m dbt`; this starts its CLI in the running interpreter.
DBT_ENTRY = "from dbt.cli.main import cli; cli()"
RUN_RESULTS = Path("target") / "run_results.json"
FAILED_STATUSES = frozenset({"fail", "error"})
FAILURE_ROWS = 20


@dataclass(frozen=True)
class Failure:
    """One dbt node that failed: its id, status, message and stored failures relation."""

    unique_id: str
    status: str
    message: str
    relation_name: str | None


def run_dbt(settings: Settings, dbt_dir: Path) -> bool:
    """Run `dbt build` (models, tests and unit tests); True when it passed.

    dbt's own output goes to stderr with the other progress lines.
    """
    (dbt_dir / RUN_RESULTS).unlink(missing_ok=True)
    command = [
        sys.executable,
        "-c",
        DBT_ENTRY,
        "build",
        "--no-use-colors",
        "--project-dir",
        str(dbt_dir),
        "--profiles-dir",
        str(dbt_dir),
    ]
    log.info("dbt build starting")
    completed = subprocess.run(
        command, env=dbt_subprocess_env(settings), stdout=sys.stderr, check=False
    )
    return completed.returncode == 0


def read_run_results(dbt_dir: Path) -> Mapping[str, Any] | None:
    """The parsed `target/run_results.json`, or None when dbt wrote none."""
    path = dbt_dir / RUN_RESULTS
    if not path.exists():
        return None
    parsed: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def failures(run_results: Mapping[str, Any]) -> tuple[Failure, ...]:
    """Every node whose status is `fail` or `error`, in run order."""
    return tuple(
        Failure(
            unique_id=result["unique_id"],
            status=result["status"],
            message=(result.get("message") or "").strip(),
            relation_name=result.get("relation_name") if _is_test(result) else None,
        )
        for result in run_results.get("results", ())
        if result.get("status") in FAILED_STATUSES
    )


def _is_test(result: Mapping[str, Any]) -> bool:
    return str(result.get("unique_id", "")).startswith("test.")


def results_summary(run_results: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    """Counts of each status per resource type (model, test, unit_test), for the run record."""
    if run_results is None:
        return {}
    counts: Counter[tuple[str, str]] = Counter(
        (str(result["unique_id"]).split(".", 1)[0], str(result["status"]))
        for result in run_results.get("results", ())
    )
    summary: dict[str, dict[str, int]] = {}
    for (kind, status), count in sorted(counts.items()):
        summary.setdefault(kind, {})[status] = count
    return summary


def format_failures(
    found: Sequence[Failure], rows: Mapping[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]
) -> str:
    """One block per failed node: its message, then up to 20 stored rows when a test has them."""
    blocks = []
    for failure in found:
        lines = [f"{failure.status.upper()} {failure.unique_id}"]
        if failure.message:
            lines.append(f"  {failure.message}")
        stored = rows.get(failure.relation_name or "")
        if stored is not None:
            columns, values = stored
            lines.append("  " + " | ".join(columns))
            lines.extend("  " + " | ".join(str(value) for value in row) for row in values)
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def stored_rows(
    conn: psycopg.Connection, relation_name: str
) -> tuple[tuple[str, ...], list[tuple[Any, ...]]]:
    """Up to 20 rows of a failing test's stored failures relation, with its column names."""
    parts = [part.strip('"') for part in relation_name.split(".")]
    cursor = conn.execute(
        sql.SQL("SELECT * FROM {} LIMIT {}").format(
            sql.Identifier(*parts[-2:]), sql.Literal(FAILURE_ROWS)
        )
    )
    columns = tuple(column.name for column in cursor.description or ())
    return columns, cursor.fetchall()


def failure_report(run_results: Mapping[str, Any] | None, conn: psycopg.Connection) -> str:
    """What failed in a dbt build, with the stored rows of each failing test."""
    if run_results is None:
        return "dbt failed before running (no target/run_results.json)"
    found = failures(run_results)
    rows = {}
    for failure in found:
        if failure.relation_name:
            try:
                rows[failure.relation_name] = stored_rows(conn, failure.relation_name)
            except psycopg.Error as error:
                log.warning("could not read %s: %s", failure.relation_name, error)
                conn.rollback()
    return format_failures(found, rows)
