"""The SQL load run record, `results/sql_load/run_record.json` (spec 0009 AC-18).

Written by every run that passes preflight, pass or fail, so a failed run never
leaves an older `ok` record in place. Status is `ok` only when the raw count,
dbt, the load consistency check and the figure gate all passed. It holds no
connection string, user name or password.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from orgos.sqlstore.figures import Consistency, FigureResult

RUN_RECORD = Path("results/sql_load/run_record.json")
VERSIONED_PACKAGES = ("dlt", "dbt-core", "dbt-postgres", "psycopg", "pymongo")
# The code that shapes a load; uncommitted changes here make the record `dirty`.
TRACKED_PATHS = ("dbt/", "src/orgos/")
STEPS = ("dlt", "raw_count", "dbt", "load_consistency", "figures")


@dataclass(frozen=True)
class RunState:
    """What a load run has learned so far; each step adds its part with `replace`."""

    dlt_load_id: str | None = None
    row_counts: Mapping[str, int] = field(default_factory=dict)
    dbt_summary: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    consistency: Consistency | None = None
    figures: tuple[FigureResult, ...] = ()
    collapsed: Mapping[str, Any] = field(default_factory=dict)
    dropped_links_per_project: Mapping[str, int] = field(default_factory=dict)
    net_check: tuple[str, ...] | None = None


def build_record(
    state: RunState,
    *,
    failed_step: str | None,
    time: datetime,
    git: Mapping[str, Any],
    versions: Mapping[str, str],
) -> dict[str, Any]:
    """The run record for `state`: status `ok` only when no step failed."""
    if failed_step is not None and failed_step not in STEPS:
        raise ValueError(f"unknown step {failed_step}")
    return {
        "time": time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "fail" if failed_step else "ok",
        "failed_step": failed_step,
        "git": dict(git),
        "dlt_load_id": state.dlt_load_id,
        "versions": dict(versions),
        "row_counts": dict(state.row_counts),
        "figures": [result.record() for result in state.figures],
        "collapsed_keys": dict(state.collapsed),
        "dropped_links_per_project": dict(state.dropped_links_per_project),
        "net_check_set": None if state.net_check is None else list(state.net_check),
        "load_consistency": None if state.consistency is None else state.consistency.record(),
        "dbt_tests": {kind: dict(counts) for kind, counts in state.dbt_summary.items()},
    }


def git_state(root: Path) -> dict[str, Any]:
    """The commit, and whether `dbt/` or `src/orgos/` hold uncommitted changes."""

    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, check=True
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return completed.stdout.strip()

    status = git("status", "--porcelain", "--", *TRACKED_PATHS)
    return {"commit": git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status)}


def package_versions() -> dict[str, str]:
    """Python and the load's package versions."""
    return {"python": platform.python_version()} | {
        name: version(name) for name in VERSIONED_PACKAGES
    }


def write_run_record(path: Path, record: Mapping[str, Any]) -> None:
    """Write the record as indented JSON, creating its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n", encoding="utf-8")
