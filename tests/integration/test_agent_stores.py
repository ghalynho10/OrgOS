"""The agent's reads and one scripted run on real stores (spec 0012 AC-8, AC-10 to AC-15, AC-18).

Postgres gets its own database and roles with the 7 `model` tables made by
hand, holding the value columns the agent lists; the graph is the shared read
only Neo4j with its one probe node. The model is a scripted `FunctionModel`:
no test calls a real model. Every query goes through the real executors, so
the login is the reader login and the graph is read only.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from neo4j import Driver
from pydantic import SecretStr
from testcontainers.community.neo4j import Neo4jContainer

from orgos.agent import build_agent
from orgos.agent.agent import OrgosQueryAgent
from orgos.agent.describe_graph import read_graph_values
from orgos.agent.describe_sql import read_sql_structure, read_sql_values
from orgos.agent.errors import AgentError
from orgos.agent.session import (
    GraphSession,
    check_sessions,
    read_graph_session,
    read_sql_session,
    session_problems,
)
from orgos.config import STORE_ROW_CAP, Settings
from orgos.eval.handoff import AgentRun, Store
from orgos.eval.runs import load_agent
from orgos.graphstore.mapping import load_mapping
from orgos.graphstore.query import reader_driver, run_cypher
from orgos.sqlstore.bootstrap import bootstrap
from orgos.sqlstore.query import QueryResult, reader_connection, run_sql
from tests.integration.conftest import NEO4J_PASSWORD
from tests.test_agent_run import QUESTION, SET_ANSWER, _agent, _scripted

pytestmark = pytest.mark.integration

LOAD_ID = "1762000000.123456"
MODEL_DDL = """
CREATE TABLE model.project (project_key text PRIMARY KEY, project_name text,
    source_doc_id text, load_id text);
CREATE TABLE model.person (person_key text PRIMARY KEY, login_name text, is_active boolean,
    source_doc_id text, load_id text);
CREATE TABLE model.fix_version (version_id text PRIMARY KEY, version_name text,
    is_released boolean, source_doc_id text, load_id text);
CREATE TABLE model.issue (
    issue_key text PRIMARY KEY,
    project_key text NOT NULL REFERENCES model.project (project_key),
    status_name text, issue_type_name text, priority_name text, resolution_name text,
    created_at timestamptz,
    source_doc_id text, load_id text
);
CREATE TABLE model.logged_time (
    history_id text PRIMARY KEY,
    person_key text NOT NULL REFERENCES model.person (person_key),
    issue_key text NOT NULL REFERENCES model.issue (issue_key),
    seconds bigint, source_doc_id text, load_id text
);
CREATE TABLE model.fixed_in (
    issue_key text NOT NULL REFERENCES model.issue (issue_key),
    version_id text NOT NULL REFERENCES model.fix_version (version_id),
    source_doc_id text, load_id text,
    PRIMARY KEY (issue_key, version_id)
);
CREATE TABLE model.issue_link (
    link_id text PRIMARY KEY,
    from_issue_key text NOT NULL REFERENCES model.issue (issue_key),
    to_issue_key text NOT NULL REFERENCES model.issue (issue_key),
    link_type_name text, outward_label text, inward_label text,
    source_doc_id text, load_id text
);
CREATE INDEX issue_project_idx ON model.issue (project_key);
CREATE INDEX issue_link_ends_idx ON model.issue_link (from_issue_key, to_issue_key);
"""
ROWS = (
    "INSERT INTO model.project VALUES ('P', 'Probe', 'd1', %(l)s)",
    "INSERT INTO model.issue VALUES"
    " ('P-1', 'P', 'Open', 'Bug', 'Major', NULL, '2013-01-01T00:00:00Z', 'd1', %(l)s),"
    " ('P-2', 'P', 'Closed', 'Bug', 'Minor', 'Fixed', '2013-01-02T00:00:00Z', 'd2', %(l)s),"
    " ('P-3', 'P', 'Open', 'Task', 'Major', NULL, NULL, 'd3', %(l)s)",
    "INSERT INTO model.issue_link VALUES"
    " ('k1', 'P-1', 'P-2', 'Blocker', 'blocks', 'is blocked by', 'd1', %(l)s),"
    " ('k2', 'P-2', 'P-3', 'Blocker', 'blocks', 'is blocked by', 'd2', %(l)s),"
    " ('k3', 'P-3', 'P-1', 'Relate', 'relates to', 'relates to', 'd3', %(l)s)",
)


@pytest.fixture(scope="module")
def agent_db(pg_settings: Settings) -> Settings:
    settings = pg_settings.model_copy(
        update={
            "pg_database": "orgos_agent",
            "pg_owner_user": "agent_owner",
            "pg_owner_password": SecretStr(secrets.token_hex(8)),
            "pg_reader_user": "agent_reader",
            "pg_reader_password": SecretStr(secrets.token_hex(8)),
        }
    )
    bootstrap(settings)
    with psycopg.connect(settings.pg_conninfo("owner"), autocommit=True) as conn:
        conn.execute(MODEL_DDL)
        for statement in ROWS:
            conn.execute(statement, {"l": LOAD_ID})
    return settings


@pytest.fixture
def reader(agent_db: Settings) -> Iterator[psycopg.Connection]:
    with reader_connection(agent_db) as conn:
        yield conn


@pytest.fixture(scope="module")
def graph(neo4j_read_only: Neo4jContainer, pg_settings: Settings) -> Iterator[Driver]:
    settings = pg_settings.model_copy(
        update={
            "neo4j_host": neo4j_read_only.get_container_host_ip(),
            "neo4j_bolt_port": int(neo4j_read_only.get_exposed_port(7687)),
            "neo4j_auth": SecretStr(f"neo4j/{NEO4J_PASSWORD}"),
        }
    )
    with reader_driver(settings) as driver:
        yield driver


@pytest.fixture(scope="module")
def both_stores(agent_db: Settings, neo4j_read_only: Neo4jContainer) -> Settings:
    """The agent database and the read only graph, with a model and a key no call ever uses."""
    return agent_db.model_copy(
        update={
            "neo4j_host": neo4j_read_only.get_container_host_ip(),
            "neo4j_bolt_port": int(neo4j_read_only.get_exposed_port(7687)),
            "neo4j_auth": SecretStr(f"neo4j/{NEO4J_PASSWORD}"),
            "model": "anthropic:claude-sonnet-5-5",
            "anthropic_api_key": SecretStr(secrets.token_hex(8)),
        }
    )


# --- build_agent on the real stores (AC-1, AC-8, AC-11, AC-24) ---


# covers: AC-1, AC-8, AC-24, the runner loads the agent from live reads and its hash is stable
def test_the_runner_loads_the_agent_from_the_real_stores_with_a_stable_hash(
    both_stores: Settings, tmp_path: Path
) -> None:
    first = load_agent("orgos.agent:build_agent", both_stores, tmp_path)
    second = build_agent(both_stores)
    assert isinstance(first, OrgosQueryAgent)
    assert first.describe().model_id == "anthropic:claude-sonnet-5-5"
    assert first.describe().version_hash == second.describe().version_hash
    assert first.describe().parts == second.describe().parts


# covers: AC-11, build_agent itself refuses a session that is not the reader, naming the check.
# The login check compares with the configured reader name, so pointing that name at the
# owner passes it; the zone, set to UTC on the reader role only, is what refuses.
def test_build_agent_refuses_the_owner_login_naming_the_check(both_stores: Settings) -> None:
    as_owner = both_stores.model_copy(
        update={
            "pg_reader_user": both_stores.pg_owner_user,
            "pg_reader_password": both_stores.pg_owner_password,
        }
    )
    with pytest.raises(AgentError, match="store session is not as required") as raised:
        build_agent(as_owner)
    assert "SQL zone:" in str(raised.value)
    assert both_stores.pg_owner_user not in str(raised.value)


# --- The session reads (AC-11) ---


def test_the_reader_and_the_read_only_graph_pass_the_session_check(
    agent_db: Settings, reader: psycopg.Connection, graph: Driver
) -> None:
    sql = read_sql_session(reader, timeout_s=5)
    graph_session = read_graph_session(graph)
    assert sql.timezone == "UTC"
    assert graph_session == GraphSession(access="read-only", timezone="Z")
    check_sessions(sql, graph_session, reader_login=agent_db.pg_reader_user)


def test_the_owner_login_is_refused(agent_db: Settings, graph: Driver) -> None:
    with psycopg.connect(agent_db.pg_conninfo("owner"), autocommit=True) as owner:
        sql = read_sql_session(owner, timeout_s=5)
    problems = session_problems(
        sql, read_graph_session(graph), reader_login=agent_db.pg_reader_user
    )
    # Only the reader role has its zone set to UTC by bootstrap, so the owner fails both.
    assert [problem.split(":")[0] for problem in problems] == ["SQL login", "SQL zone"]


def test_a_session_zone_other_than_utc_is_refused(
    agent_db: Settings, reader: psycopg.Connection, graph: Driver
) -> None:
    reader.execute("SET TIME ZONE 'America/St_Johns'")
    sql = read_sql_session(reader, timeout_s=5)
    problems = session_problems(
        sql, read_graph_session(graph), reader_login=agent_db.pg_reader_user
    )
    assert problems == ("SQL zone: the session zone is 'America/St_Johns', not UTC",)


# --- The structure and value reads (AC-8, AC-10) ---


def test_the_catalog_read_gives_keys_and_indexes(reader: psycopg.Connection) -> None:
    tables = {table.name: table for table in read_sql_structure(reader, timeout_s=5)}
    assert sorted(tables) == [
        "fix_version",
        "fixed_in",
        "issue",
        "issue_link",
        "logged_time",
        "person",
        "project",
    ]
    assert tables["fixed_in"].primary_key == ("issue_key", "version_id")
    assert [(k.columns, k.table) for k in tables["issue"].foreign_keys] == [
        (("project_key",), "project")
    ]
    assert ("from_issue_key", "to_issue_key") in tables["issue_link"].indexes
    created = next(c for c in tables["issue"].columns if c.name == "created_at")
    assert created.data_type == "timestamp with time zone"


def test_the_value_lists_are_sorted_and_distinct(reader: psycopg.Connection) -> None:
    values = read_sql_values(reader, timeout_s=5)
    assert values.values["issue.status_name"] == ("Closed", "Open")
    assert values.values["issue.resolution_name"] == ("Fixed",)
    assert [(link.name, link.outward, link.inward) for link in values.link_types] == [
        ("Blocker", "blocks", "is blocked by"),
        ("Relate", "relates to", "relates to"),
    ]


def test_the_graph_value_read_runs_on_a_graph_without_issues(graph: Driver) -> None:
    values = read_graph_values(graph, load_mapping(), timeout_s=5)
    assert all(listed == () for listed in values.values.values())
    assert values.link_types == ()


# --- One scripted run through the real executors (AC-12 to AC-15, AC-18) ---


def _sql(
    conn: psycopg.Connection, timeout_s: float = 5, row_cap: int = STORE_ROW_CAP
) -> Callable[[str], QueryResult]:
    return lambda statement: run_sql(conn, statement, timeout_s=timeout_s, row_cap=row_cap)


def _run(executor: Callable[[str], QueryResult], *steps: Any, store: Store = "sql") -> AgentRun:
    agent = _agent(_scripted(*steps), **{store: executor})
    return agent.run(QUESTION, store)


def test_a_scripted_run_answers_from_the_full_sql_rows(reader: psycopg.Connection) -> None:
    statement = "SELECT 'X-' || g AS id FROM generate_series(1, 60) AS g ORDER BY g"
    run = _run(_sql(reader), statement, SET_ANSWER)
    assert run.outcome == "answered"
    assert len(run.value) == 60
    assert run.value[-1] == "issue:X-60"
    assert run.sources == ({"issue": tuple(f"X-{n}" for n in range(1, 61))},)


def test_a_zero_row_result_keeps_its_columns_and_can_be_pointed_at(
    reader: psycopg.Connection,
) -> None:
    run = _run(_sql(reader), "SELECT issue_key AS id FROM model.issue WHERE false", SET_ANSWER)
    assert run.outcome == "answered"
    assert run.value == []
    assert run.sources == ()
    assert run.attempts[0].rows == 0


def test_a_write_is_refused_by_the_store(agent_db: Settings, reader: psycopg.Connection) -> None:
    run = _run(_sql(reader), "DELETE FROM model.issue", "text:could not")
    assert run.attempts[0].outcome == "error"
    with psycopg.connect(agent_db.pg_conninfo("owner")) as owner:
        assert owner.execute("SELECT count(*) FROM model.issue").fetchone() == (3,)


def test_two_columns_of_one_name_are_an_error(reader: psycopg.Connection) -> None:
    run = _run(_sql(reader), "SELECT issue_key AS id, project_key AS id FROM model.issue", "text:x")
    assert run.attempts[0].outcome == "error"


def test_a_timeout_and_a_cap_hit_are_labelled(reader: psycopg.Connection) -> None:
    slow = _run(_sql(reader, timeout_s=1), "SELECT pg_sleep(5) AS value", "text:slow")
    assert slow.attempts[0].outcome == "store_timeout"
    assert slow.outcome == "store_timeout"
    capped = _run(_sql(reader, row_cap=2), "SELECT issue_key AS id FROM model.issue", "text:many")
    assert capped.attempts[0].outcome == "row_cap"


def test_a_store_error_text_holds_no_connection_detail(
    agent_db: Settings, reader: psycopg.Connection
) -> None:
    seen: list[str] = []
    steps = _scripted("SELECT nope FROM model.issue", "text:x")

    def script(messages: list[Any], info: Any) -> Any:
        seen.extend(
            str(part.content) for part in messages[-1].parts if part.part_kind == "tool-return"
        )
        return steps(messages, info)

    agent = _agent(script, sql=_sql(reader))
    agent.run(QUESTION, "sql")
    text = seen[0]
    assert text.startswith("error, no handle:")
    secret_values = (
        agent_db.pg_reader_user,
        agent_db.pg_reader_password.get_secret_value(),
        agent_db.pg_host,
        str(agent_db.pg_port),
    )
    for detail in secret_values:
        assert detail not in text


def test_the_graph_run_rejects_a_denied_statement_and_a_write(graph: Driver) -> None:
    def cypher(statement: str) -> QueryResult:
        return run_cypher(graph, statement, timeout_s=5)

    answer = {
        **SET_ANSWER,
        "answer_result": "r3",
        "sources": [{"issue": {"result": "r3", "column": "id"}}],
    }
    run = _run(
        cypher,
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "CREATE (:Probe {id: 2})",
        "MATCH (n:Probe) RETURN toString(n.id) AS id",
        answer,
        store="graph",
    )
    assert [a.outcome for a in run.attempts] == ["rejected", "error", "ok"]
    assert run.outcome == "answered"
    assert run.value == ["issue:1"]
    assert run_cypher(graph, "MATCH (n) RETURN count(n) AS nodes", timeout_s=5).rows == (
        {"nodes": 1},
    )
