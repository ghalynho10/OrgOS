"""The shared preflight: every store query in the eval runs on one checked load (spec 0011 AC-1).

`preflight` runs five checks in a fixed order and stops at the first that
fails with an `EvalError` naming it: the frozen set, the two load run records,
the SQL state as the reader login, the graph state, and the schema fingerprint.
`orgos eval preflight`, `gold try`, `run` and the query agent (feature 13) all
call this one function. It holds no copy of the rules it calls: the freeze rule
is `orgos.eval.frozen`, the records rule and the graph rule are
`orgos.graphstore.checks`, and the SQL rule is `orgos.sqlstore.checks`.

The fingerprint (AC-6) is the `model` index definitions and the graph's
constraints and indexes, each without its name. The first `gold try` writes it
with an exclusive create; no code rewrites it, and every later preflight
refuses when an index or constraint was added or removed since.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neo4j.exceptions import DriverError, Neo4jError

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.frozen import ANSWERS, FROZEN, frozen_problems, parse_frozen, tagged_frozen
from orgos.graphstore.checks import (
    GraphState,
    check_run_records,
    graph_state_problems,
    read_graph_state,
    schema_descriptions,
)
from orgos.graphstore.mapping import load_mapping
from orgos.graphstore.query import reader_driver
from orgos.graphstore.record import RUN_RECORD as GRAPH_RUN_RECORD
from orgos.sqlstore import SqlStoreError
from orgos.sqlstore.checks import SqlState, read_sql_state, sql_state_problems
from orgos.sqlstore.query import reader_connection
from orgos.sqlstore.record import RUN_RECORD as SQL_RUN_RECORD

log = logging.getLogger(__name__)

FINGERPRINT = Path("results/gold/schema_fingerprint.json")
# The checks in the order they run (AC-1).
FROZEN_SET = "frozen set"
RUN_RECORDS = "run records"
SQL_STATE = "SQL state"
GRAPH_STATE = "graph state"
SCHEMA_FINGERPRINT = "schema fingerprint"


@dataclass(frozen=True)
class Fingerprint:
    """The schema both stores are queried against, each item described by what it is."""

    sql_indexes: tuple[str, ...]
    graph_constraints: tuple[str, ...]
    graph_indexes: tuple[str, ...]

    def record(self) -> dict[str, list[str]]:
        """The JSON object written to `schema_fingerprint.json`."""
        return {
            "sql_indexes": list(self.sql_indexes),
            "graph_constraints": list(self.graph_constraints),
            "graph_indexes": list(self.graph_indexes),
        }


@dataclass(frozen=True)
class Preflight:
    """What a passing preflight checked: load ID L, the live fingerprint, whether it is saved."""

    load_id: str
    fingerprint: Fingerprint
    fingerprint_saved: bool


def refuse(check: str, problems: tuple[str, ...]) -> EvalError:
    """The `EvalError` for a failing check, naming it and listing every problem."""
    return EvalError(f"Preflight refused at the {check} check:\n  " + "\n  ".join(problems))


def frozen_set_problems(root: Path) -> tuple[str, ...]:
    """The freeze rule of `orgos.eval.frozen`, plus `eval/FROZEN` listing the answers (AC-2)."""
    problems = frozen_problems(root, tagged_frozen(root))
    frozen = root / FROZEN
    listed = (
        [path for _, path in parse_frozen(frozen.read_text(encoding="utf-8"))[0]]
        if frozen.is_file()
        else []
    )
    if ANSWERS not in listed:
        problems += (f"{FROZEN} does not list {ANSWERS}",)
    return problems


def _read_record(path: Path) -> Mapping[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise refuse(RUN_RECORDS, (f"cannot read {path}: {error.strerror}",)) from None
    except json.JSONDecodeError as error:
        raise refuse(RUN_RECORDS, (f"{path} is not valid JSON: {error}",)) from None
    if not isinstance(parsed, Mapping):
        raise refuse(RUN_RECORDS, (f"{path} does not hold a JSON object",))
    return parsed


def fingerprint_of(sql_state: SqlState, graph_state: GraphState) -> Fingerprint:
    """The live fingerprint from what the SQL and graph state checks already read."""
    constraints, indexes = schema_descriptions(graph_state)
    return Fingerprint(
        sql_indexes=sql_state.indexes, graph_constraints=constraints, graph_indexes=indexes
    )


def parse_fingerprint(text: str) -> Fingerprint:
    """A saved fingerprint, or an `EvalError` naming the file when it is malformed."""
    try:
        data = json.loads(text)
        return Fingerprint(
            sql_indexes=tuple(str(item) for item in data["sql_indexes"]),
            graph_constraints=tuple(str(item) for item in data["graph_constraints"]),
            graph_indexes=tuple(str(item) for item in data["graph_indexes"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise refuse(SCHEMA_FINGERPRINT, (f"{FINGERPRINT} is malformed: {error}",)) from None


def fingerprint_problems(saved: Fingerprint, live: Fingerprint) -> tuple[str, ...]:
    """Each index or constraint added or removed since the fingerprint was saved."""
    problems: list[str] = []
    for kind, before, now in (
        ("SQL index", saved.sql_indexes, live.sql_indexes),
        ("graph constraint", saved.graph_constraints, live.graph_constraints),
        ("graph index", saved.graph_indexes, live.graph_indexes),
    ):
        problems.extend(f"{kind} added: {item}" for item in now if item not in before)
        problems.extend(f"{kind} removed: {item}" for item in before if item not in now)
    return tuple(problems)


def write_fingerprint(root: Path, fingerprint: Fingerprint) -> None:
    """Write the fingerprint once, with an exclusive create that fails if the file exists."""
    path = root / FINGERPRINT
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(fingerprint.record(), handle, indent=2)
            handle.write("\n")
    except FileExistsError:
        raise EvalError(f"{FINGERPRINT} already exists and is never rewritten") from None


def _sql_state(settings: Settings) -> SqlState:
    try:
        with reader_connection(settings) as conn:
            return read_sql_state(conn)
    except SqlStoreError as error:
        raise refuse(SQL_STATE, (str(error),)) from None


def _graph_state(settings: Settings) -> GraphState:
    try:
        with reader_driver(settings) as driver:
            return read_graph_state(driver)
    except (Neo4jError, DriverError) as error:
        raise refuse(GRAPH_STATE, (f"cannot read the graph: {type(error).__name__}",)) from None


def record_load_id(root: Path) -> str:
    """The run records check (AC-3): both records `ok` with one `dlt_load_id`, which is L.

    Reads only the two record files, so `gold accept` learns L without a store.
    """
    sql_record = _read_record(root / SQL_RUN_RECORD)
    graph_record = _read_record(root / GRAPH_RUN_RECORD)
    problems = check_run_records(
        (str(SQL_RUN_RECORD), sql_record), (str(GRAPH_RUN_RECORD), graph_record)
    )
    if problems:
        raise refuse(RUN_RECORDS, problems)
    return str(sql_record["dlt_load_id"])


def preflight(settings: Settings, root: Path) -> Preflight:
    """Run the five checks in order and stop at the first that fails; writes nothing."""
    problems = frozen_set_problems(root)
    if problems:
        raise refuse(FROZEN_SET, problems)
    log.info("preflight: the frozen set passed")

    load_id = record_load_id(root)
    log.info("preflight: the run records passed, load ID %s", load_id)

    sql_state = _sql_state(settings)
    problems = sql_state_problems(sql_state, load_id)
    if problems:
        raise refuse(SQL_STATE, problems)
    log.info("preflight: the SQL state passed")

    graph_state = _graph_state(settings)
    problems = graph_state_problems(graph_state, load_mapping(), load_id)
    if problems:
        raise refuse(GRAPH_STATE, problems)
    log.info("preflight: the graph state passed")

    live = fingerprint_of(sql_state, graph_state)
    path = root / FINGERPRINT
    saved = path.is_file()
    if saved:
        problems = fingerprint_problems(parse_fingerprint(path.read_text(encoding="utf-8")), live)
        if problems:
            raise refuse(SCHEMA_FINGERPRINT, problems)
    log.info("preflight: the schema fingerprint %s", "passed" if saved else "is not written yet")
    return Preflight(load_id=load_id, fingerprint=live, fingerprint_saved=saved)


def summary(result: Preflight) -> str:
    """What a passing preflight checked, one line per check, for stdout."""
    fingerprint = (
        f"matches {FINGERPRINT}"
        if result.fingerprint_saved
        else f"not written yet (the first gold try writes {FINGERPRINT})"
    )
    return "\n".join(
        (
            "preflight ok",
            f"  {FROZEN_SET}: {FROZEN} holds and lists {ANSWERS}",
            f"  {RUN_RECORDS}: both say ok and hold load ID {result.load_id}",
            f"  {SQL_STATE}: the 7 tables, one load_id, the 10 foreign keys, zone UTC,"
            " read as the reader login",
            f"  {GRAPH_STATE}: constraints and indexes as the mapping, one loadId, neo4j read-only",
            f"  {SCHEMA_FINGERPRINT}: {fingerprint}",
        )
    )
