"""The graph load core on the corpus fixtures, on real Postgres and Neo4j (spec 0010 AC-24).

The real dlt hints and dbt project build `model_int` from `tests/fixtures/corpus/`
in a Postgres container, the way the SQL load tests do. `load_into` writes it to
a writable Neo4j; the checks then run on the same volume restarted read only,
as `orgos load graph` runs them. The load time failures change one row of the
rows read from `model_int`, and the check time failures change the graph
itself between two restarts.

The fixtures run in order: the writable loads first, then the checks of the
good graph, then the checks of the changed graph.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import docker
import pytest
from neo4j import Driver
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from pydantic import SecretStr
from testcontainers.community.neo4j import Neo4jContainer

from orgos.config import Settings
from orgos.graphstore import GraphStoreError
from orgos.graphstore.checks import (
    FoundCounts,
    expected_counts,
    graph_state_problems,
    read_counts,
    read_graph_state,
    read_temporal,
    reconcile,
    temporal_problems,
)
from orgos.graphstore.compare import Comparison
from orgos.graphstore.load import (
    ModelInt,
    TableRows,
    compare_graph,
    load_into,
    read_model_int,
    wipe,
)
from orgos.graphstore.mapping import GraphMapping, load_mapping
from orgos.graphstore.statements import SHOW_DATABASE
from orgos.ingest import to_raw_row
from orgos.ingest.load import load_raw
from orgos.sqlstore.bootstrap import bootstrap
from orgos.sqlstore.dbt import run_dbt

from .conftest import NEO4J_IMAGE
from .test_sql_load_fixtures import DBT_DIR, _good_docs

pytestmark = pytest.mark.integration

MAPPING = load_mapping()
ISSUE_LINK = next(rel for rel in MAPPING.relationships if rel.type_column is not None)
DATABASE = "neo4j"


@pytest.fixture(scope="module")
def gx(pg_settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Its own database and roles, with `model_int` built from the good fixtures."""
    settings = pg_settings.model_copy(
        update={
            "pg_database": "orgos_graph",
            "pg_owner_user": "graph_owner",
            "pg_owner_password": SecretStr(secrets.token_hex(8)),
            "pg_reader_user": "graph_reader",
            "pg_reader_password": SecretStr(secrets.token_hex(8)),
        }
    )
    bootstrap(settings)
    pipelines = tmp_path_factory.mktemp("graph-dlt")
    load_raw(settings.pg_owner_url, (to_raw_row(doc) for doc in _good_docs()), pipelines)
    assert run_dbt(settings, DBT_DIR) == 0
    return settings


@pytest.fixture(scope="module")
def model_int(gx: Settings) -> ModelInt:
    return read_model_int(gx.pg_conninfo("owner"), MAPPING)


def _load_id(model_int: ModelInt) -> str:
    return str(model_int.tables["issue"].dicts()[0]["load_id"])


@contextmanager
def graph(volume: str, password: str, *, read_only: bool, heap: str = "512m") -> Iterator[Driver]:
    """A driver to Neo4j on `volume`, with the compose transaction cap, read only when asked.

    It waits until `neo4j` is online with the asked access, as the load's own
    poll does: the Bolt port opens before the database does.
    """
    container = (
        Neo4jContainer(NEO4J_IMAGE, password=password)
        .with_env("NEO4J_server_memory_heap_max__size", heap)
        .with_env("NEO4J_server_memory_pagecache_size", "64m")
        .with_env("NEO4J_db_memory_transaction_max", "256M")
        .with_volume_mapping(volume, "/data", "rw")
    )
    if read_only:
        container = container.with_env("NEO4J_server_databases_read__only", DATABASE)
    access = "read-only" if read_only else "read-write"
    with container, container.get_driver() as driver:
        deadline = time.monotonic() + 60
        while True:
            try:
                rows = driver.execute_query(
                    SHOW_DATABASE, name=DATABASE, database_="system"
                ).records
            except (ServiceUnavailable, SessionExpired, TransientError):
                rows = []
            state = (rows[0]["currentStatus"], rows[0]["access"]) if rows else None
            if state == ("online", access):
                break
            assert time.monotonic() < deadline, f"{DATABASE} is {state}, not online and {access}"
            time.sleep(0.5)
        yield driver
        # A clean stop, as compose does: testcontainers kills the container, and a
        # read only start cannot run the recovery an unclean stop leaves behind.
        container.get_wrapped_container().stop(timeout=60)


@pytest.fixture(scope="module")
def volume() -> Iterator[tuple[str, str]]:
    """One data volume for every restart of this module, and the password made for it."""
    name = f"orgos-test-graph-{uuid.uuid4().hex[:8]}"
    try:
        yield name, secrets.token_hex(8)
    finally:
        docker.from_env().volumes.get(name).remove(force=True)


def _with_row(model_int: ModelInt, table: str, index: int, **changes: Any) -> ModelInt:
    # `model_int` with one row of one table changed, by column name.
    rows = model_int.tables[table]
    row = dict(zip(rows.columns, rows.rows[index], strict=True)) | changes
    changed = tuple(row[column] for column in rows.columns)
    new_rows = (*rows.rows[:index], changed, *rows.rows[index + 1 :])
    tables = {**model_int.tables, table: TableRows(rows.columns, new_rows)}
    return replace(model_int, tables=tables)


def _failure(driver: Driver, model_int: ModelInt) -> str:
    with pytest.raises(GraphStoreError) as raised:
        load_into(driver, MAPPING, model_int, _load_id(model_int))
    return str(raised.value)


def _found(driver: Driver) -> FoundCounts:
    return read_counts(driver, MAPPING)


@dataclass(frozen=True)
class Writable:
    """What the writable phase saw: the counts of two good loads and each failure message."""

    first: FoundCounts
    second: FoundCounts
    load_ids: tuple[str | None, ...]
    failures: dict[str, str]
    picked: dict[str, str]


@pytest.fixture(scope="module")
def writable(volume: tuple[str, str], model_int: ModelInt) -> Writable:
    """Two good loads, the five load time failures, then a final good load."""
    name, password = volume
    load_id = _load_id(model_int)
    in_project = model_int.tables["in_project"].dicts()[0]
    link = model_int.tables["issue_link"].dicts()[0]
    issue = model_int.tables["issue"].dicts()[0]
    with graph(name, password, read_only=False) as driver:
        load_into(driver, MAPPING, model_int, load_id)
        first = _found(driver)
        load_into(driver, MAPPING, model_int, load_id)
        second = _found(driver)
        load_ids = read_graph_state(driver).node_load_ids
        failures = {
            "unmatched": _failure(driver, _with_row(model_int, "in_project", 0, dst_id="NOPE")),
            "edge_type": _failure(driver, _with_row(model_int, "issue_link", 0, edge_type="Odd")),
            "empty": _failure(driver, _with_row(model_int, "issue", 0, status_name="")),
            "naive": _failure(
                driver, _with_row(model_int, "issue", 0, created_at=datetime(2013, 1, 1))
            ),
        }
        driver.execute_query(
            "CREATE INDEX stray FOR (n:Issue) ON (n.statusName)", database_=DATABASE
        )
        failures["stray"] = _failure(driver, model_int)
        # The load never drops an index, so the test removes its own.
        driver.execute_query("DROP INDEX stray", database_=DATABASE)
        load_into(driver, MAPPING, model_int, load_id)
    picked = {
        "in_project": str(in_project["edge_id"]),
        "issue_link": str(link["edge_id"]),
        "issue": str(issue["issue_key"]),
    }
    return Writable(first, second, load_ids, failures, picked)


@dataclass(frozen=True)
class Checked:
    """Every check of the load, as run on the database restarted read only."""

    state: tuple[str, ...]
    reconciliation: tuple[str, ...]
    temporal: tuple[str, ...]
    compare: Comparison


def _checks(gx: Settings, driver: Driver, mapping: GraphMapping, model_int: ModelInt) -> Checked:
    expected = expected_counts(mapping, model_int.table_rows, model_int.type_rows)
    return Checked(
        state=graph_state_problems(read_graph_state(driver), mapping, _load_id(model_int)),
        reconciliation=reconcile(mapping, expected, read_counts(driver, mapping)).problems,
        temporal=temporal_problems(read_temporal(driver, mapping)),
        compare=compare_graph(gx.pg_conninfo("owner"), driver, mapping),
    )


@pytest.fixture(scope="module")
def good(gx: Settings, volume: tuple[str, str], model_int: ModelInt, writable: Writable) -> Checked:
    name, password = volume
    with graph(name, password, read_only=True) as driver:
        return _checks(gx, driver, MAPPING, model_int)


# A second IN_PROJECT with the edgeId of the first row, from the start of another one.
REPEAT_EDGE_ID = (
    "MATCH (a:Issue)-[r:IN_PROJECT {edgeId: $edge}]->(p:Project) "
    "MATCH (other:Issue) WHERE other <> a "
    "WITH r, p, other LIMIT 1 "
    "CREATE (other)-[c:IN_PROJECT]->(p) SET c = properties(r)"
)
CHANGE_STATUS = "MATCH (n:Issue {issueKey: $issue}) SET n.statusName = 'Changed'"
NAME_THE_ZONE = "MATCH (n:Issue {issueKey: $issue}) SET n.createdAt = $named"


@pytest.fixture(scope="module")
def tampered(
    gx: Settings, volume: tuple[str, str], model_int: ModelInt, writable: Writable, good: Checked
) -> Checked:
    """The good graph with four changes, checked on the database restarted read only."""
    name, password = volume
    edge, issue, link = (writable.picked[key] for key in ("in_project", "issue", "issue_link"))
    with graph(name, password, read_only=False) as driver:
        driver.execute_query(REPEAT_EDGE_ID, edge=edge, database_=DATABASE)
        driver.execute_query(CHANGE_STATUS, issue=issue, database_=DATABASE)
        _reverse(driver, link)
        driver.execute_query(
            NAME_THE_ZONE,
            issue=issue,
            named=_created_at(model_int, issue).astimezone(ZoneInfo("Etc/UTC")),
            database_=DATABASE,
        )
    with graph(name, password, read_only=True) as driver:
        return _checks(gx, driver, MAPPING, model_int)


def _reverse(driver: Driver, edge_id: str) -> None:
    # Delete one link and create it again from its end to its start.
    link_types = [kind for _, kind in ISSUE_LINK.types]
    records = driver.execute_query(
        "MATCH (a:Issue)-[r {edgeId: $edge}]->(b:Issue) WHERE type(r) IN $types "
        "RETURN a.issueKey AS a, b.issueKey AS b, type(r) AS kind, properties(r) AS props",
        edge=edge_id,
        types=link_types,
        database_=DATABASE,
    ).records
    (record,) = records
    kind = str(record["kind"])
    assert kind in link_types
    driver.execute_query(
        "MATCH ()-[r {edgeId: $edge}]->() WHERE type(r) = $kind DELETE r",
        edge=edge_id,
        kind=kind,
        database_=DATABASE,
    )
    driver.execute_query(
        f"MATCH (a:Issue {{issueKey: $a}}), (b:Issue {{issueKey: $b}}) "
        f"CREATE (b)-[r:{kind}]->(a) SET r = $props",
        a=record["a"],
        b=record["b"],
        props=record["props"],
        database_=DATABASE,
    )


def _created_at(model_int: ModelInt, issue: str) -> datetime:
    row = next(row for row in model_int.tables["issue"].dicts() if row["issue_key"] == issue)
    value = row["created_at"]
    assert isinstance(value, datetime)
    return value


# covers: AC-11, AC-15 (a good load matches every model_int count, three ways)
def test_a_good_load_reconciles(model_int: ModelInt, writable: Writable) -> None:
    expected = expected_counts(MAPPING, model_int.table_rows, model_int.type_rows)
    assert reconcile(MAPPING, expected, writable.first).problems == ()
    assert writable.first.labels["Issue"] == (270, 270)
    assert writable.first.types["HAS_PARENT"] == (1, 1)


# covers: AC-9 (a second load ends with the same counts and one loadId)
def test_a_second_load_has_the_same_counts(model_int: ModelInt, writable: Writable) -> None:
    assert writable.second == writable.first
    assert set(writable.load_ids) == {_load_id(model_int)}


# covers: AC-12 (an end that matches no node is named by table and edge_id)
def test_an_unmatched_end_is_named(writable: Writable) -> None:
    message = writable.failures["unmatched"]
    assert message.startswith("in_project: ")
    assert message.endswith(f"matched no node: edge_id {writable.picked['in_project']}")


# covers: AC-11 (an unlisted edge_type is named by table, edge_id and value)
def test_an_unlisted_edge_type_is_named(writable: Writable) -> None:
    assert writable.failures["edge_type"] == (
        f"issue_link row {writable.picked['issue_link']}: edge_type 'Odd'"
        " is not one of the 17 link types in the mapping"
    )


# covers: AC-13 (an empty string and a datetime with no zone are named by table and key)
def test_an_empty_string_and_a_naive_datetime_are_named(writable: Writable) -> None:
    issue = writable.picked["issue"]
    assert writable.failures["empty"] == (
        f"issue row {issue}: status_name is an empty string; model_int stores a null instead"
    )
    assert (
        writable.failures["naive"]
        == f"issue row {issue}: created_at is a datetime with no time zone"
    )


# covers: AC-10 (a stray index stops the load, named)
def test_a_stray_index_is_named(writable: Writable) -> None:
    assert writable.failures["stray"] == (
        "The graph schema is not the expected one:\n"
        "  unexpected index stray: RANGE on NODE Issue(statusName)"
    )


# covers: AC-15 to AC-18 (the good graph, restarted read only, passes every check)
def test_the_good_graph_passes_every_check(model_int: ModelInt, good: Checked) -> None:
    assert good.state == ()
    assert good.reconciliation == ()
    assert good.temporal == ()
    assert good.compare.problems == ()
    assert good.compare.nodes == sum(model_int.table_rows[node.table] for node in MAPPING.nodes)
    assert good.compare.relationships == sum(
        model_int.table_rows[rel.table] for rel in MAPPING.relationships
    )


# covers: AC-15, AC-18 (a repeated edgeId with other ends is found by both)
def test_a_repeated_edge_id_is_found(
    model_int: ModelInt, writable: Writable, tampered: Checked
) -> None:
    rows = model_int.table_rows["in_project"]
    assert f"IN_PROJECT: {rows:,} model_int rows, {rows + 1:,} found, {rows:,} distinct" in (
        tampered.reconciliation
    )
    repeated = tampered.compare.differences["repeated key"]
    assert repeated.shown == (f"in_project {writable.picked['in_project']}",)


# covers: AC-18 (a changed property and a reversed relationship are found)
def test_a_changed_property_and_a_reversed_link_are_found(
    writable: Writable, tampered: Checked
) -> None:
    differences = tampered.compare.differences
    assert differences["node property"].shown == (f"Issue {writable.picked['issue']} statusName",)
    # The repeated IN_PROJECT may add its own entry, whichever copy is read first.
    link = f"issue_link {writable.picked['issue_link']}: "
    assert any(text.startswith(link) for text in differences["relationship ends"].shown)


# covers: AC-16 (a datetime stored with a named zone is found; the compare sees the same instant)
def test_a_named_zone_is_found_by_the_temporal_check(writable: Writable, tampered: Checked) -> None:
    assert tampered.temporal == (
        f"Issue.createdAt: 1 values not in the Z form ({writable.picked['issue']})",
    )
    assert "Issue.createdAt" not in " ".join(tampered.compare.problems)
    assert tampered.state == ()


HUB_DEGREE = 60_000


# covers: AC-25 (the two step wipe empties a node holding 60,000 relationships under the
# compose transaction cap; whether DETACH DELETE fails is a measurement in rationale.md)
def test_the_two_step_wipe_empties_a_hub_under_the_transaction_cap() -> None:
    name = f"orgos-test-wipe-{uuid.uuid4().hex[:8]}"
    try:
        with graph(name, secrets.token_hex(8), read_only=False, heap="1g") as driver:
            (cap,) = driver.execute_query(
                "SHOW SETTINGS YIELD name, value WHERE name = 'db.memory.transaction.max' "
                "RETURN value",
                database_=DATABASE,
            ).records
            assert str(cap["value"]).startswith("256")
            driver.execute_query("CREATE (:Hub)", database_=DATABASE)
            with driver.session(database=DATABASE) as session:
                session.run(
                    "UNWIND range(1, $n) AS i CALL (i) "
                    "{ MATCH (h:Hub) CREATE (h)-[:LINK]->(:Leaf {i: i}) } "
                    "IN TRANSACTIONS OF 10000 ROWS",
                    n=HUB_DEGREE,
                ).consume()
            (hub,) = driver.execute_query(
                "MATCH (:Hub)-[r]->() RETURN count(r) AS degree", database_=DATABASE
            ).records
            assert hub["degree"] == HUB_DEGREE
            wipe(driver, MAPPING, ModelInt({}, {}, {}), "unused")
            (left,) = driver.execute_query(
                "MATCH (n) OPTIONAL MATCH (n)-[r]-() RETURN count(n) AS nodes, count(r) AS rels",
                database_=DATABASE,
            ).records
            assert (left["nodes"], left["rels"]) == (0, 0)
    finally:
        docker.from_env().volumes.get(name).remove(force=True)
