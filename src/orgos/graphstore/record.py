"""The graph load run record, `results/graph_load/run_record.json` (spec 0010 AC-6, AC-21).

Written as `fail` with step `started` before Neo4j is first made writable, and
as `ok` only as the last act of a run where every step passed, so anything that
needs "the graph is good" reads this file. It holds no connection string, user
name or password.
"""

from __future__ import annotations

import platform
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from orgos.graphstore.checks import Reconciliation

RUN_RECORD = Path("results/graph_load/run_record.json")
VERSIONED_PACKAGES = ("neo4j", "psycopg")
# The step names a record can carry as its failed step, in run order.
STEPS = (
    "started",
    "writable",
    "wipe",
    "constraints",
    "nodes",
    "relationships",
    "read_only",
    "checks",
    "compare",
)


@dataclass(frozen=True)
class RunState:
    """What a graph load has learned so far; each step adds its part with `replace`."""

    dlt_load_id: str
    read_only_restored: bool | None = None
    neo4j_server: str | None = None
    constraints: tuple[Mapping[str, Any], ...] | None = None
    indexes: tuple[Mapping[str, Any], ...] | None = None
    reconciliation: Reconciliation | None = None
    seconds: Mapping[str, float] = field(default_factory=dict)


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
        "versions": {**versions, "neo4j_server": state.neo4j_server},
        "read_only_restored": state.read_only_restored,
        "show_constraints": None if state.constraints is None else list(state.constraints),
        "show_indexes": None if state.indexes is None else list(state.indexes),
        "reconciliation": None if state.reconciliation is None else state.reconciliation.record(),
        "seconds": {step: round(seconds, 1) for step, seconds in state.seconds.items()},
    }


def package_versions() -> dict[str, str]:
    """Python and the load's package versions; the server version comes from Neo4j itself."""
    return {"python": platform.python_version()} | {
        name: version(name) for name in VERSIONED_PACKAGES
    }
