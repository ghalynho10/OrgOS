"""`orgos load graph`: `model_int` into Neo4j, restarted read only, then checked (spec 0010).

The order is fixed: preflight (writes nothing), the run record written as
`fail`, the `model_int` reads, Neo4j made writable, the wipe, the constraints,
the nodes, the relationships, Neo4j made read only again, the checks and the
value level compare on the restarted database, and the record written as `ok`
as the last act. Once Neo4j was made writable it is made read only again
whatever happens, Ctrl+C included. Progress goes to stderr as log lines; the
summary this returns goes to stdout.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from neo4j import Driver, GraphDatabase, ManagedTransaction, Session, SummaryCounters
from neo4j.exceptions import DriverError, Neo4jError
from psycopg import IsolationLevel, sql

from orgos.config import Settings
from orgos.graphstore import (
    DATABASE,
    RETRY_SECONDS,
    SHOWN,
    WRITE_BATCH_ROWS,
    GraphStoreError,
)
from orgos.graphstore.checks import (
    check_run_records,
    expected_counts,
    graph_schema_problems,
    graph_state_problems,
    read_counts,
    read_graph_state,
    read_temporal,
    reconcile,
    temporal_problems,
)
from orgos.graphstore.compare import (
    Comparison,
    compare_nodes,
    compare_relationships,
    comparison,
    read_nodes,
    read_relationships,
)
from orgos.graphstore.compose import docker_problems, set_access
from orgos.graphstore.convert import NodeRow, RelationshipRow, node_row, relationship_row, type_of
from orgos.graphstore.mapping import LOAD_ID, GraphMapping, RelationshipMap, load_mapping
from orgos.graphstore.record import RUN_RECORD, RunState, build_record, package_versions
from orgos.graphstore.statements import (
    SERVER_VERSION,
    create_constraint,
    existing_keys,
    node_merge,
    relationship_merge,
    wipe_nodes,
    wipe_relationships,
)
from orgos.sqlstore.figures import Consistency, load_consistency
from orgos.sqlstore.record import RUN_RECORD as SQL_RUN_RECORD
from orgos.sqlstore.record import git_state, write_run_record

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10
SCHEMA = "model_int"

# Errors a step can raise that become a `GraphStoreError` naming the step (AC-1).
STEP_ERRORS = (
    GraphStoreError,
    psycopg.Error,
    Neo4jError,
    DriverError,
    OSError,
    subprocess.SubprocessError,
)


@dataclass(frozen=True)
class Inputs:
    """What preflight checked: the load ID to load and the mapping."""

    load_id: str
    mapping: GraphMapping


@dataclass(frozen=True)
class TableRows:
    """One `model_int` table as read: its columns (from the mapping) and its rows."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def dicts(self) -> list[dict[str, Any]]:
        """Each row as a column to value map."""
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


@dataclass(frozen=True)
class ModelInt:
    """The 12 tables read in one snapshot, with the counts of the same snapshot."""

    tables: Mapping[str, TableRows]
    table_rows: Mapping[str, int]
    type_rows: Mapping[str, int]


def _read_record(path: Path) -> Mapping[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise GraphStoreError(
            f"Cannot read {path}: {error.strerror}; run `uv run orgos load sql` first"
        ) from None
    except json.JSONDecodeError as error:
        raise GraphStoreError(f"{path} is not valid JSON: {error}") from None
    if not isinstance(parsed, Mapping):
        raise GraphStoreError(f"{path} does not hold a JSON object")
    return parsed


def consistency_problems(consistency: Consistency, load_id: str) -> tuple[str, ...]:
    """The load consistency problems, plus a latest load that is not `load_id` (AC-3, AC-19).

    That line names both IDs and every table that does not hold `load_id`.
    """
    if consistency.latest_load_id == load_id:
        return consistency.problems
    tables = [table.table for table in consistency.tables if table.load_ids != (load_id,)]
    return (
        *consistency.problems,
        f"the latest load is {consistency.latest_load_id}, the SQL record holds {load_id};"
        f" tables not holding {load_id}: {', '.join(tables) or 'none'}",
    )


def preflight(settings: Settings, root: Path) -> Inputs:
    """Check the SQL record, the live load consistency, the mapping and Docker; write nothing."""
    record = _read_record(root / SQL_RUN_RECORD)
    problems = check_run_records((str(SQL_RUN_RECORD), record), None)
    if problems:
        raise GraphStoreError("The SQL load is not ready:\n  " + "\n  ".join(problems))
    load_id = str(record["dlt_load_id"])

    try:
        with psycopg.connect(
            settings.pg_conninfo("owner"), connect_timeout=CONNECT_TIMEOUT_S
        ) as conn:
            consistency = load_consistency(conn)
    except psycopg.Error as error:
        raise GraphStoreError(f"preflight step failed: {error}") from None
    problems = consistency_problems(consistency, load_id)
    if problems:
        raise GraphStoreError(
            "The live load consistency check does not match the SQL record:\n  "
            + "\n  ".join(problems)
        )
    log.info("load consistency passed for all %s tables", len(consistency.tables))

    mapping = load_mapping()
    problems = docker_problems(root)
    if problems:
        raise GraphStoreError("Docker is not ready:\n  " + "\n  ".join(problems))
    log.info("preflight passed")
    return Inputs(load_id=load_id, mapping=mapping)


def _snapshot(conninfo: str) -> psycopg.Connection:
    # A read only `REPEATABLE READ` connection, so one transaction is one snapshot (AC-5).
    conn = psycopg.connect(conninfo, connect_timeout=CONNECT_TIMEOUT_S)
    conn.isolation_level = IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    return conn


def read_table(conn: psycopg.Connection, table: str, columns: tuple[str, ...]) -> TableRows:
    """The columns the mapping names of one `model_int` table, ordered by the first; no join."""
    rows = conn.execute(
        sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.Identifier(SCHEMA, table),
            sql.Identifier(columns[0]),
        )
    ).fetchall()
    return TableRows(columns=columns, rows=tuple(rows))


def read_model_int(conninfo: str, mapping: GraphMapping) -> ModelInt:
    """Read the 12 tables and their counts in one read only `REPEATABLE READ` snapshot (AC-5)."""
    with _snapshot(conninfo) as conn, conn.transaction():
        tables = {}
        table_rows = {}
        reads = [(node.table, node.read_columns) for node in mapping.nodes] + [
            (rel.table, rel.read_columns) for rel in mapping.relationships
        ]
        for table, columns in reads:
            tables[table] = read_table(conn, table, columns)
            count = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(SCHEMA, table))
            ).fetchone()
            table_rows[table] = int(count[0]) if count else 0
        type_rows = {}
        for rel in mapping.relationships:
            if rel.type_column is not None:
                grouped = conn.execute(
                    sql.SQL("SELECT {0}, count(*) FROM {1} GROUP BY {0}").format(
                        sql.Identifier(rel.type_column), sql.Identifier(SCHEMA, rel.table)
                    )
                ).fetchall()
                type_rows = {str(value): int(rows) for value, rows in grouped}
    log.info(
        "read %s model_int rows from %s tables",
        f"{sum(table_rows.values()):,}",
        len(tables),
    )
    return ModelInt(tables=tables, table_rows=table_rows, type_rows=type_rows)


def _driver(settings: Settings) -> Driver:
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        max_transaction_retry_time=RETRY_SECONDS,
    )


def _write_batch(
    tx: ManagedTransaction, statement: str, rows: Sequence[NodeRow] | Sequence[RelationshipRow]
) -> SummaryCounters:
    return tx.run(statement, rows=rows).consume().counters


def _read_keys(tx: ManagedTransaction, statement: str, keys: list[str]) -> set[str]:
    return {str(record["key"]) for record in tx.run(statement, keys=keys)}


def wipe(driver: Driver, mapping: GraphMapping, model_int: ModelInt, load_id: str) -> None:
    """Delete every relationship, then every node, in autocommit statements never retried (AC-9).

    AC-14: `CALL ... IN TRANSACTIONS` needs autocommit, and a rerun repeats the wipe.
    """
    with driver.session(database=DATABASE, disable_auto_commit_retries=True) as session:
        relationships = session.run(wipe_relationships()).consume().counters
        nodes = session.run(wipe_nodes()).consume().counters
    log.info(
        "wiped %s relationships and %s nodes",
        f"{relationships.relationships_deleted:,}",
        f"{nodes.nodes_deleted:,}",
    )


def constraints(driver: Driver, mapping: GraphMapping, model_int: ModelInt, load_id: str) -> None:
    """Create the four constraints if missing, then require exactly the expected schema (AC-10)."""
    for node in mapping.nodes:
        driver.execute_query(create_constraint(node), database_=DATABASE)
    problems = graph_schema_problems(read_graph_state(driver), mapping)
    if problems:
        raise GraphStoreError(
            "The graph schema is not the expected one:\n  " + "\n  ".join(problems)
        )
    log.info("the four constraints are in place and no other index exists")


def _check_load_id(table: str, key: str, row: Mapping[str, Any], load_id: str) -> None:
    # Every row must carry the load ID preflight checked (spec 0008 AC-14).
    if row[LOAD_ID] != load_id:
        raise GraphStoreError(f"{table} row {key} holds load_id {row[LOAD_ID]}, expected {load_id}")


def nodes(driver: Driver, mapping: GraphMapping, model_int: ModelInt, load_id: str) -> None:
    """Merge the four node tables by key, 5,000 rows per transaction (AC-11)."""
    with driver.session(database=DATABASE) as session:
        for node in mapping.nodes:
            rows: list[NodeRow] = []
            for row in model_int.tables[node.table].dicts():
                converted = node_row(node, row)
                _check_load_id(node.table, str(converted["key"]), row, load_id)
                rows.append(converted)
            statement = node_merge(node)
            for start in range(0, len(rows), WRITE_BATCH_ROWS):
                session.execute_write(
                    _write_batch, statement, rows[start : start + WRITE_BATCH_ROWS]
                )
            log.info("wrote %s %s nodes", f"{len(rows):,}", node.label)


def _unmatched(
    session: Session, relationship: RelationshipMap, batch: Sequence[RelationshipRow]
) -> list[str]:
    # The edge IDs of a batch whose start or end key matched no node (AC-12).
    starts = session.execute_read(
        _read_keys, existing_keys(relationship.start), sorted({row["src"] for row in batch})
    )
    ends = session.execute_read(
        _read_keys, existing_keys(relationship.end), sorted({row["dst"] for row in batch})
    )
    return [row["edgeId"] for row in batch if row["src"] not in starts or row["dst"] not in ends]


def _write_relationships(
    session: Session, relationship: RelationshipMap, kind: str, rows: list[RelationshipRow]
) -> None:
    statement = relationship_merge(kind, relationship.start, relationship.end)
    for start in range(0, len(rows), WRITE_BATCH_ROWS):
        batch = rows[start : start + WRITE_BATCH_ROWS]
        created = session.execute_write(_write_batch, statement, batch).relationships_created
        if created == len(batch):
            continue
        missed = _unmatched(session, relationship, batch)
        where = f"{relationship.table}: {created:,} of {len(batch):,} {kind} relationships created"
        if not missed:
            raise GraphStoreError(
                f"{where}; every start and end exists, so a row merged into an earlier one"
                " (a repeated edge_id?)"
            )
        more = len(missed) - SHOWN
        raise GraphStoreError(
            f"{where}; the start or end of these rows matched no node: edge_id "
            + ", ".join(missed[:SHOWN])
            + (f" and {more:,} more" if more > 0 else "")
        )


def relationships(driver: Driver, mapping: GraphMapping, model_int: ModelInt, load_id: str) -> None:
    """Merge the eight relationship tables by `edgeId`, one statement per type (AC-11, AC-12)."""
    with driver.session(database=DATABASE) as session:
        for relationship in mapping.relationships:
            by_type: dict[str, list[RelationshipRow]] = {
                kind: [] for kind in relationship.relationship_types
            }
            for row in model_int.tables[relationship.table].dicts():
                converted = relationship_row(relationship, row)
                _check_load_id(relationship.table, str(converted["edgeId"]), row, load_id)
                by_type[type_of(relationship, row)].append(converted)
            for kind, rows in by_type.items():
                if rows:
                    _write_relationships(session, relationship, kind, rows)
                    log.info("wrote %s %s relationships", f"{len(rows):,}", kind)


LoadStep = Callable[[Driver, GraphMapping, ModelInt, str], None]
# The writes, in the order spec 0008 fixes: wipe, constraints, nodes, relationships.
LOAD_STEPS: tuple[tuple[str, LoadStep], ...] = (
    ("wipe", wipe),
    ("constraints", constraints),
    ("nodes", nodes),
    ("relationships", relationships),
)


def load_into(driver: Driver, mapping: GraphMapping, model_int: ModelInt, load_id: str) -> None:
    """The writes alone, on a writable database: wipe, constraints, nodes, relationships."""
    for _, step in LOAD_STEPS:
        step(driver, mapping, model_int, load_id)


@dataclass(frozen=True)
class _StepFailed:
    # A step's error (or Ctrl+C) and the step it happened in.
    step: str
    error: BaseException

    def named(self) -> GraphStoreError:
        """The error to raise: a `GraphStoreError` as it is, anything else named by its step."""
        if isinstance(self.error, GraphStoreError):
            return self.error
        return GraphStoreError(f"{self.step} step failed: {self.error}")


def _timed(state: RunState, step: str, started: float) -> RunState:
    return replace(state, seconds={**state.seconds, step: time.monotonic() - started})


def _restore(settings: Settings, root: Path) -> bool:
    # Make Neo4j read only again; never raises, so it runs after any failure.
    # No signal handler: a second Ctrl+C stops it and is reported as not restored.
    try:
        set_access(settings, root, read_only=True)
    except (*STEP_ERRORS, KeyboardInterrupt) as error:
        log.error("could not make Neo4j read only again: %s", error or type(error).__name__)
        return False
    return True


def _write_graph(
    settings: Settings, root: Path, inputs: Inputs, state: RunState
) -> tuple[RunState, _StepFailed | None, ModelInt | None]:
    # From the `model_int` reads to Neo4j read only again.
    step, failure, model_int, writable = "started", None, None, False
    try:
        started = time.monotonic()
        model_int = read_model_int(settings.pg_conninfo("owner"), inputs.mapping)
        state = _timed(state, step, started)

        step, started, writable = "writable", time.monotonic(), True
        set_access(settings, root, read_only=False)
        state = _timed(state, step, started)

        with _driver(settings) as driver:
            for step, write in LOAD_STEPS:
                started = time.monotonic()
                write(driver, inputs.mapping, model_int, inputs.load_id)
                state = _timed(state, step, started)
    except (*STEP_ERRORS, KeyboardInterrupt) as error:
        failure = _StepFailed(step, error)
    if writable:
        started = time.monotonic()
        restored = _restore(settings, root)
        state = _timed(replace(state, read_only_restored=restored), "read_only", started)
        if failure is None and not restored:
            failure = _StepFailed(
                "read_only", GraphStoreError("Neo4j was not made read only again")
            )
    # Only the counts are kept: the compare reads its own snapshot.
    return state, failure, None if model_int is None else replace(model_int, tables={})


def _failed_checks(checks: Mapping[str, Sequence[str]]) -> str:
    return "\n  ".join(
        f"{name}: {problem}" for name, problems in checks.items() for problem in problems
    )


def _check(
    settings: Settings, inputs: Inputs, state: RunState, model_int: ModelInt
) -> tuple[RunState, _StepFailed | None]:
    # The checks, all on the restarted read only database (AC-15 to AC-17, AC-19).
    started = time.monotonic()
    try:
        with _driver(settings) as driver:
            observed = read_graph_state(driver)
            found = read_counts(driver, inputs.mapping)
            temporal = read_temporal(driver, inputs.mapping)
            server = driver.execute_query(SERVER_VERSION, database_=DATABASE).records
        with psycopg.connect(
            settings.pg_conninfo("owner"), connect_timeout=CONNECT_TIMEOUT_S
        ) as conn:
            consistency = load_consistency(conn)
        expected = expected_counts(inputs.mapping, model_int.table_rows, model_int.type_rows)
        reconciliation = reconcile(inputs.mapping, expected, found)
        checks = {
            "reconciliation": reconciliation.problems,
            "temporal": temporal_problems(temporal),
            "graph_state": graph_state_problems(observed, inputs.mapping, inputs.load_id),
            "load_consistency": consistency_problems(consistency, inputs.load_id),
        }
        state = replace(
            state,
            neo4j_server=str(server[0]["version"]) if server else None,
            constraints=observed.constraints,
            indexes=observed.indexes,
            reconciliation=reconciliation,
            checks={
                name: problems for name, problems in checks.items() if name != "reconciliation"
            },
        )
        if any(checks.values()):
            raise GraphStoreError("The checks failed:\n  " + _failed_checks(checks))
        log.info(
            "checks passed: %s nodes and %s relationships reconcile, every datetime is in the"
            " Z form, one loadId, read only, load consistency holds",
            f"{reconciliation.nodes:,}",
            f"{reconciliation.relationships:,}",
        )
    except (*STEP_ERRORS, KeyboardInterrupt) as error:
        return _timed(state, "checks", started), _StepFailed("checks", error)
    return _timed(state, "checks", started), None


def compare_graph(conninfo: str, driver: Driver, mapping: GraphMapping) -> Comparison:
    """The value level compare (AC-18) over its own read only `REPEATABLE READ` snapshot.

    One table at a time: its rows and the graph items it maps to are read,
    compared and dropped before the next, so memory stays bounded by the
    largest table.
    """
    differences = []
    nodes_read = relationships_read = 0
    with _snapshot(conninfo) as conn, conn.transaction():
        for node in mapping.nodes:
            found_nodes = read_nodes(driver, node)
            rows = read_table(conn, node.table, node.read_columns).dicts()
            differences.extend(compare_nodes(node, rows, found_nodes))
            nodes_read += len(found_nodes)
        for relationship in mapping.relationships:
            found_relationships = read_relationships(driver, relationship)
            rows = read_table(conn, relationship.table, relationship.read_columns).dicts()
            differences.extend(compare_relationships(relationship, rows, found_relationships))
            relationships_read += len(found_relationships)
    return comparison(nodes_read, relationships_read, differences)


def _compare(
    settings: Settings, inputs: Inputs, state: RunState
) -> tuple[RunState, _StepFailed | None]:
    # The value level compare, on the restarted read only database (AC-18).
    started = time.monotonic()
    try:
        with _driver(settings) as driver:
            result = compare_graph(settings.pg_conninfo("owner"), driver, inputs.mapping)
        state = replace(state, comparison=result)
        if result.problems:
            raise GraphStoreError(
                "The value level compare found differences:\n  " + "\n  ".join(result.problems)
            )
        log.info(
            "compared %s nodes and %s relationships with model_int: no differences",
            f"{result.nodes:,}",
            f"{result.relationships:,}",
        )
    except (*STEP_ERRORS, KeyboardInterrupt) as error:
        return _timed(state, "compare", started), _StepFailed("compare", error)
    return _timed(state, "compare", started), None


def _write(root: Path, state: RunState, failed_step: str | None) -> dict[str, Any]:
    record = build_record(
        state,
        failed_step=failed_step,
        time=datetime.now(UTC),
        git=git_state(root),
        versions=package_versions(),
    )
    try:
        write_run_record(root / RUN_RECORD, record)
    except OSError as error:
        raise GraphStoreError(f"Cannot write {RUN_RECORD}: {error.strerror}") from None
    log.info("wrote %s with status %s", RUN_RECORD, record["status"])
    return record


def load_graph(settings: Settings, root: Path) -> str:
    """Load the graph from `model_int`, restart Neo4j read only, check it; return the summary.

    Raises `GraphStoreError` for a preflight refusal, a failed step or a failed
    check. After preflight the run record says `fail` until the very end, and a
    Ctrl+C is raised again once Neo4j was made read only and the record written.
    """
    inputs = preflight(settings, root)
    state = RunState(dlt_load_id=inputs.load_id)
    _write(root, state, "started")
    state, failure, model_int = _write_graph(settings, root, inputs, state)
    if failure is None and model_int is not None:
        state, failure = _check(settings, inputs, state, model_int)
    if failure is None:
        state, failure = _compare(settings, inputs, state)
    if failure is not None:
        _write(root, state, failure.step)
        writable = (
            f"Neo4j may still be writable: the restart to read only failed"
            f" (read_only_restored is false in {RUN_RECORD})."
        )
        if isinstance(failure.error, KeyboardInterrupt):
            if state.read_only_restored is False:
                log.error(writable)
            raise failure.error
        error = failure.named()
        if state.read_only_restored is False:
            raise GraphStoreError(f"{error}\n{writable}") from None
        raise error from None
    return summary(_write(root, state, None))


def summary(record: Mapping[str, Any]) -> str:
    """The plain stdout summary: status, load ID, the count of each label and type."""
    reconciliation = record["reconciliation"]
    counts = {**reconciliation["labels"], **reconciliation["relationship_types"]}
    width = max((len(name) for name in counts), default=0)
    lines = [f"orgos load graph: {record['status']}", f"dlt load {record['dlt_load_id']}"]
    lines.extend(f"  {name:<{width}}  {count['found']:>9,}" for name, count in counts.items())
    lines.append(
        f"{reconciliation['total_nodes']:,} nodes, "
        f"{reconciliation['total_relationships']:,} relationships"
    )
    compared = record.get("compare")
    if compared is not None:
        lines.append(
            f"compared {compared['nodes_compared']:,} nodes and"
            f" {compared['relationships_compared']:,} relationships with model_int,"
            f" {sum(kind['count'] for kind in compared['differences'].values()):,} differences"
        )
    lines.append(f"run record: {RUN_RECORD}")
    return "\n".join(lines)
