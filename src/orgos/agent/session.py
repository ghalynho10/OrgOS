"""The session check of both stores, run by `build_agent` before any other read (spec 0012 AC-11).

The agent only reads, and every time it compares is UTC, so before it reads
structure or values it checks what each store's session actually is. SQL: the
login is the reader login and the session zone is `UTC`. Graph: database
`neo4j` is `read-only` and the server zone `db.temporal.timezone` is `Z`.
`read_sql_session` and `read_graph_session` are the only functions here that
touch a store; `session_problems` is pure and judges what they returned, and
`check_sessions` turns any problem into one `AgentError` naming each check.
Messages name the setting to look at, never a user name.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from neo4j import Driver

from orgos.agent.errors import AgentError
from orgos.graphstore import DATABASE
from orgos.graphstore.checks import READ_ONLY, UTC_FORM
from orgos.graphstore.statements import PREFIX, SHOW_DATABASE
from orgos.sqlstore.checks import TIMEZONE
from orgos.sqlstore.query import run_sql

# `current_setting('TimeZone')` is the value `SHOW timezone` prints; a
# `SELECT` because the executor's server side cursor takes only a query.
SQL_SESSION = "SELECT current_user AS login, current_setting('TimeZone') AS timezone"
GRAPH_TIMEZONE_SETTING = "db.temporal.timezone"
SHOW_TIMEZONE_SETTING = f"{PREFIX} SHOW SETTINGS YIELD name, value WHERE name = $name RETURN value"


@dataclass(frozen=True)
class SqlSession:
    """What the SQL session is: its login and its zone."""

    login: str
    timezone: str


@dataclass(frozen=True)
class GraphSession:
    """What the graph session sees: the access of `neo4j` and the server zone, `None` if absent."""

    access: str | None
    timezone: str | None


def read_sql_session(conn: psycopg.Connection, *, timeout_s: float) -> SqlSession:
    """Read the login and zone of the reader connection, through the one SQL executor."""
    row = run_sql(conn, SQL_SESSION, timeout_s=timeout_s).rows[0]
    return SqlSession(login=str(row["login"]), timezone=str(row["timezone"]))


def read_graph_session(driver: Driver) -> GraphSession:
    """Read the access of `neo4j` and the server's `db.temporal.timezone`, from `system`."""
    databases = driver.execute_query(SHOW_DATABASE, {"name": DATABASE}, database_="system")
    settings = driver.execute_query(
        SHOW_TIMEZONE_SETTING, {"name": GRAPH_TIMEZONE_SETTING}, database_="system"
    )
    return GraphSession(
        access=str(databases.records[0]["access"]) if databases.records else None,
        timezone=str(settings.records[0]["value"]) if settings.records else None,
    )


def session_problems(sql: SqlSession, graph: GraphSession, *, reader_login: str) -> tuple[str, ...]:
    """Every session check that fails, one line each, in a fixed order."""
    problems = []
    if sql.login != reader_login:
        problems.append("SQL login: the session is not the reader login (ORGOS_PG_READER_USER)")
    if sql.timezone != TIMEZONE:
        problems.append(f"SQL zone: the session zone is {sql.timezone!r}, not {TIMEZONE}")
    if graph.access != READ_ONLY:
        shown = "absent" if graph.access is None else repr(graph.access)
        problems.append(f"graph access: database {DATABASE} is {shown}, not {READ_ONLY}")
    if graph.timezone != UTC_FORM:
        shown = "unset" if graph.timezone is None else repr(graph.timezone)
        problems.append(f"graph zone: {GRAPH_TIMEZONE_SETTING} is {shown}, not {UTC_FORM}")
    return tuple(problems)


def check_sessions(sql: SqlSession, graph: GraphSession, *, reader_login: str) -> None:
    """Raise `AgentError` naming every failed session check; return quietly when all pass."""
    problems = session_problems(sql, graph, reader_login=reader_login)
    if problems:
        raise AgentError(
            "The agent refuses to start, a store session is not as required:\n  "
            + "\n  ".join(problems)
        )
