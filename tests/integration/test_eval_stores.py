"""The two executors and the SQL state reader on real stores (spec 0011 AC-4, AC-8 to AC-10).

Postgres gets its own database and roles, so the probe tables other modules add
to `model` cannot meet the exactly 7 tables check. The 7 tables are created by
hand as the owner with the 10 foreign keys of spec 0007 AC-12; nothing here
needs the corpus.
"""

import secrets
import time
from collections.abc import Iterator
from types import SimpleNamespace

import psycopg
import pytest
from neo4j import Driver
from pydantic import SecretStr
from testcontainers.community.neo4j import Neo4jContainer

from orgos.config import Settings
from orgos.graphstore.query import (
    GraphQueryError,
    GraphQueryTimeout,
    GraphRowCapError,
    reader_driver,
    run_cypher,
)
from orgos.sqlstore import query
from orgos.sqlstore.bootstrap import bootstrap
from orgos.sqlstore.checks import read_sql_state, sql_state_problems
from orgos.sqlstore.query import (
    SERVER_TIMEOUT_MARGIN_S,
    SqlConnectionLost,
    SqlQueryError,
    SqlQueryTimeout,
    SqlRowCapError,
    reader_connection,
    run_sql,
)
from tests.integration.conftest import NEO4J_PASSWORD

pytestmark = pytest.mark.integration

LOAD_ID = "1762000000.123456"
MODEL_DDL = """
CREATE TABLE model.project (project_key text PRIMARY KEY, source_doc_id text, load_id text);
CREATE TABLE model.person (person_key text PRIMARY KEY, source_doc_id text, load_id text);
CREATE TABLE model.fix_version (version_id text PRIMARY KEY, source_doc_id text, load_id text);
CREATE TABLE model.issue (
    issue_key text PRIMARY KEY,
    project_key text NOT NULL REFERENCES model.project (project_key),
    assignee_key text REFERENCES model.person (person_key),
    reporter_key text REFERENCES model.person (person_key),
    creator_key text REFERENCES model.person (person_key),
    parent_key text,
    source_doc_id text,
    load_id text
);
CREATE TABLE model.logged_time (
    history_id text PRIMARY KEY,
    person_key text NOT NULL REFERENCES model.person (person_key),
    issue_key text NOT NULL REFERENCES model.issue (issue_key),
    seconds bigint,
    source_doc_id text,
    load_id text
);
CREATE TABLE model.fixed_in (
    issue_key text NOT NULL REFERENCES model.issue (issue_key),
    version_id text NOT NULL REFERENCES model.fix_version (version_id),
    source_doc_id text,
    load_id text,
    PRIMARY KEY (issue_key, version_id)
);
CREATE TABLE model.issue_link (
    link_id text PRIMARY KEY,
    from_issue_key text NOT NULL REFERENCES model.issue (issue_key),
    to_issue_key text NOT NULL REFERENCES model.issue (issue_key),
    source_doc_id text,
    load_id text
);
CREATE INDEX issue_status_idx ON model.issue (project_key);
"""
ROWS = (
    "INSERT INTO model.project VALUES ('P', 'd1', %(l)s)",
    "INSERT INTO model.person VALUES ('u1', 'd1', %(l)s)",
    "INSERT INTO model.fix_version VALUES ('v1', 'd1', %(l)s)",
    "INSERT INTO model.issue VALUES ('P-1', 'P', 'u1', 'u1', 'u1', NULL, 'd1', %(l)s),"
    " ('P-2', 'P', NULL, 'u1', 'u1', 'P-1', 'd2', %(l)s)",
    "INSERT INTO model.logged_time VALUES ('h1', 'u1', 'P-1', 3600, 'd1', %(l)s)",
    "INSERT INTO model.fixed_in VALUES ('P-1', 'v1', 'd1', %(l)s)",
    "INSERT INTO model.issue_link VALUES ('k1', 'P-1', 'P-2', 'd1', %(l)s)",
)


@pytest.fixture(scope="module")
def eval_db(pg_settings: Settings) -> Settings:
    settings = pg_settings.model_copy(
        update={
            "pg_database": "orgos_eval",
            "pg_owner_user": "eval_owner",
            "pg_owner_password": SecretStr(secrets.token_hex(8)),
            "pg_reader_user": "eval_reader",
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
def owner(eval_db: Settings) -> Iterator[psycopg.Connection]:
    with psycopg.connect(eval_db.pg_conninfo("owner"), autocommit=True) as conn:
        yield conn


@pytest.fixture
def reader(eval_db: Settings) -> Iterator[psycopg.Connection]:
    with reader_connection(eval_db) as conn:
        yield conn


def sql_problems(settings: Settings) -> tuple[str, ...]:
    with reader_connection(settings) as conn:
        return sql_state_problems(read_sql_state(conn), LOAD_ID)


# --- The SQL state reader (AC-4) ---


def test_the_reader_sees_a_good_model(eval_db: Settings) -> None:
    with reader_connection(eval_db) as conn:
        state = read_sql_state(conn)
    assert sql_state_problems(state, LOAD_ID) == ()
    assert "CREATE INDEX ON model.issue USING btree (project_key)" in state.indexes


def test_a_leftover_dbt_tmp_table_is_seen(eval_db: Settings, owner: psycopg.Connection) -> None:
    owner.execute("CREATE TABLE model.issue__dbt_tmp (issue_key text)")
    try:
        assert sql_problems(eval_db) == (
            "model.issue__dbt_tmp is left over from a dbt build that stopped partway",
        )
    finally:
        owner.execute("DROP TABLE model.issue__dbt_tmp")


def test_a_second_load_id_is_seen(eval_db: Settings, owner: psycopg.Connection) -> None:
    owner.execute("INSERT INTO model.project VALUES ('Q', 'd9', '1761000000.1')")
    try:
        assert sql_problems(eval_db) == (
            f"model.project holds load_id 1761000000.1, {LOAD_ID}, expected only {LOAD_ID}",
        )
    finally:
        owner.execute("DELETE FROM model.project WHERE project_key = 'Q'")


def test_a_dropped_foreign_key_is_seen(eval_db: Settings, owner: psycopg.Connection) -> None:
    owner.execute("ALTER TABLE model.fixed_in DROP CONSTRAINT fixed_in_version_id_fkey")
    try:
        assert sql_problems(eval_db) == (
            "foreign key missing: fixed_in.version_id to fix_version.version_id",
        )
    finally:
        owner.execute(
            "ALTER TABLE model.fixed_in ADD FOREIGN KEY (version_id)"
            " REFERENCES model.fix_version (version_id)"
        )


def test_a_view_is_seen(eval_db: Settings, owner: psycopg.Connection) -> None:
    owner.execute("CREATE VIEW model.issue_view AS SELECT issue_key FROM model.issue")
    try:
        assert sql_problems(eval_db) == ("model.issue_view (relkind v) is not one of the 7 tables",)
    finally:
        owner.execute("DROP VIEW model.issue_view")


# --- The SQL executor (AC-8, AC-10) ---


def test_rows_come_back_as_dicts_with_their_columns(reader: psycopg.Connection) -> None:
    result = run_sql(
        reader,
        "SELECT issue_key, parent_key FROM model.issue ORDER BY issue_key",
        timeout_s=5,
    )
    assert result.columns == ("issue_key", "parent_key")
    assert result.rows == (
        {"issue_key": "P-1", "parent_key": None},
        {"issue_key": "P-2", "parent_key": "P-1"},
    )


def test_a_result_with_no_rows_keeps_its_columns(reader: psycopg.Connection) -> None:
    result = run_sql(reader, "SELECT issue_key, load_id FROM model.issue WHERE false", timeout_s=5)
    assert result.columns == ("issue_key", "load_id")
    assert result.rows == ()


def test_a_session_zone_is_utc(reader: psycopg.Connection) -> None:
    assert run_sql(reader, "SELECT current_setting('TimeZone') AS zone", timeout_s=5).rows == (
        {"zone": "UTC"},
    )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO model.project VALUES ('X', 'd', 'l')",
        "CREATE TABLE model.reader_table (x int)",
        "WITH gone AS (DELETE FROM model.project RETURNING 1) SELECT * FROM gone",
        "SELECT 1; SELECT 2",
    ],
)
def test_a_write_or_two_statements_are_rejected(
    reader: psycopg.Connection, owner: psycopg.Connection, statement: str
) -> None:
    with pytest.raises(SqlQueryError):
        run_sql(reader, statement, timeout_s=5)
    assert owner.execute("SELECT count(*) FROM model.project").fetchone() == (1,)
    assert run_sql(reader, "SELECT 1 AS ok", timeout_s=5).rows == ({"ok": 1},)


def test_a_statement_past_the_timeout_is_cancelled_from_the_client(
    reader: psycopg.Connection,
) -> None:
    started = time.monotonic()
    with pytest.raises(SqlQueryTimeout, match="ran past the 1 s timeout"):
        run_sql(reader, "SELECT pg_sleep(4)", timeout_s=1)
    # Before the server backstop at 2 s: the client's cancel is the one that fired.
    assert time.monotonic() - started < 1 + SERVER_TIMEOUT_MARGIN_S
    assert run_sql(reader, "SELECT 1 AS ok", timeout_s=5).rows == ({"ok": 1},)


class NeverFires:
    """A client timer whose cancel never reaches the server."""

    def __init__(self, *_: object) -> None:
        self.daemon = False

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass


# covers: the review finding on the SQL timeout (a server side backstop, same label)
def test_the_server_stops_a_statement_when_the_client_cancel_fails(
    eval_db: Settings, reader: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_sql(
        reader, "SELECT current_setting('statement_timeout') AS t", timeout_s=5
    ).rows == ({"t": "6s"},)
    monkeypatch.setattr(query, "threading", SimpleNamespace(Timer=NeverFires))
    started = time.monotonic()
    # The reader's own role timeout here is longer than the 4 s sleep: the 2 s stop is SET LOCAL.
    assert eval_db.query_timeout_s > 4
    with pytest.raises(SqlQueryTimeout, match="ran past the 1 s timeout"):
        run_sql(reader, "SELECT pg_sleep(4)", timeout_s=1)
    assert time.monotonic() - started < 3
    # SET LOCAL ends with the transaction: the session is back on the role's setting.
    assert reader.execute("SELECT current_setting('statement_timeout')").fetchone() == (
        f"{eval_db.query_timeout_s}s",
    )


def test_the_row_cap_refuses_one_row_more_and_keeps_none(reader: psycopg.Connection) -> None:
    at_cap = run_sql(reader, "SELECT g FROM generate_series(1, 10) AS g", timeout_s=5, row_cap=10)
    assert len(at_cap.rows) == 10
    with pytest.raises(SqlRowCapError, match="more than 10 rows"):
        run_sql(reader, "SELECT g FROM generate_series(1, 11) AS g", timeout_s=5, row_cap=10)
    assert run_sql(reader, "SELECT 1 AS ok", timeout_s=5).rows == ({"ok": 1},)


def test_a_refused_login_names_neither_the_user_nor_the_password(eval_db: Settings) -> None:
    password = secrets.token_hex(8)
    wrong = eval_db.model_copy(update={"pg_reader_password": SecretStr(password)})
    with pytest.raises(SqlConnectionLost) as excinfo, reader_connection(wrong):
        pass
    message = str(excinfo.value)
    assert wrong.pg_reader_user not in message
    assert password not in message


# --- The graph executor (AC-9, AC-10) ---


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


def test_records_come_back_as_dicts_with_their_columns(graph: Driver) -> None:
    result = run_cypher(graph, "MATCH (n:Probe) RETURN n.id AS id, 'x' AS tag", timeout_s=5)
    assert result.columns == ("id", "tag")
    assert result.rows == ({"id": 1, "tag": "x"},)


def test_a_graph_result_with_no_rows_keeps_its_columns(graph: Driver) -> None:
    result = run_cypher(graph, "MATCH (n:Nothing) RETURN n.id AS id, n.name AS name", timeout_s=5)
    assert result.columns == ("id", "name")
    assert result.rows == ()


def test_a_create_is_rejected_while_read_only(graph: Driver) -> None:
    with pytest.raises(GraphQueryError):
        run_cypher(graph, "CREATE (:Probe {id: 2})", timeout_s=5)
    assert run_cypher(graph, "MATCH (n) RETURN count(n) AS nodes", timeout_s=5).rows == (
        {"nodes": 1},
    )


def test_a_graph_statement_past_the_timeout_is_stopped(graph: Driver) -> None:
    slow = (
        "UNWIND range(1, 20000) AS a UNWIND range(1, 20000) AS b"
        " WITH a, b WHERE a * b = -1 RETURN count(*) AS n"
    )
    started = time.monotonic()
    with pytest.raises(GraphQueryTimeout, match="ran past the 1 s timeout"):
        run_cypher(graph, slow, timeout_s=1)
    assert time.monotonic() - started < 10


def test_the_graph_row_cap_refuses_one_record_more(graph: Driver) -> None:
    at_cap = run_cypher(graph, "UNWIND range(1, 10) AS x RETURN x", timeout_s=5, row_cap=10)
    assert len(at_cap.rows) == 10
    with pytest.raises(GraphRowCapError, match="more than 10 rows"):
        run_cypher(graph, "UNWIND range(1, 11) AS x RETURN x", timeout_s=5, row_cap=10)
