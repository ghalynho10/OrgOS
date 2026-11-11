"""The one way an eval command or the agent runs a statement on Postgres (spec 0011 AC-8).

Each call runs one statement as the reader login inside `BEGIN READ ONLY`,
cancels it from the client at the query timeout, with `statement_timeout` set
a little later as a server side backstop should the cancel fail (both end in
the same `store_timeout` label), and reads its rows one fetch
at a time through a server side cursor, stopping at `STORE_ROW_CAP` plus one
row. A server side cursor, not a stream, because a stream that returns no rows
also returns no column names, and a result must keep its columns. A server side
cursor only takes `SELECT` or `VALUES`, so a write is refused by the database
as a syntax error before the read only transaction is reached. The result is
whole or an error, never a partial result. `QueryResult` is shared with the
graph executor, so both stores answer in one shape.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

from orgos.config import STORE_ROW_CAP, Settings
from orgos.sqlstore import SqlStoreError

CONNECT_TIMEOUT_S = 10
CURSOR_NAME = "orgos_query"
# The server stops a statement this long after the client would, so the client's
# cancel is the one that normally fires and the server's is only a backstop.
SERVER_TIMEOUT_MARGIN_S = 1.0
# Rows per round trip of the server side cursor; memory stays bounded by the cap.
FETCH_ROWS = 2_000


class SqlQueryTimeout(SqlStoreError):
    """Raised when a statement runs past the query timeout and is cancelled."""


class SqlRowCapError(SqlStoreError):
    """Raised when a statement returns more than `STORE_ROW_CAP` rows."""


class SqlConnectionLost(SqlStoreError):
    """Raised when Postgres cannot be reached or the connection drops (a store outage)."""


class SqlQueryError(SqlStoreError):
    """Raised when Postgres refuses a statement: bad syntax, no permission, a write."""


@dataclass(frozen=True)
class QueryResult:
    """One statement's result: its column names in order, and every row as a column to value map.

    A result with no rows still has its columns. Two columns of one name keep
    both names in `columns`, and the later value in each row map.
    """

    columns: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]


def row_cap_message(store: str, row_cap: int) -> str:
    """The row cap error text, the same words for both stores."""
    return f"the {store} query returned more than {row_cap:,} rows, the row cap; no rows are kept"


@contextmanager
def reader_connection(settings: Settings) -> Iterator[psycopg.Connection]:
    """A connection as the reader login, every transaction on it read only."""
    try:
        conn = psycopg.connect(settings.pg_conninfo("reader"), connect_timeout=CONNECT_TIMEOUT_S)
    except psycopg.OperationalError:
        # The driver's message names the login; say only what to check.
        raise SqlConnectionLost(
            "Cannot connect to Postgres as the reader login; check the stack is up"
            " and `uv run orgos bootstrap` ran"
        ) from None
    with conn:
        conn.read_only = True
        yield conn


def run_sql(
    conn: psycopg.Connection,
    statement: str,
    *,
    timeout_s: float,
    row_cap: int = STORE_ROW_CAP,
) -> QueryResult:
    """Run one statement read only and return its columns and every row, or raise a named error."""
    timer = threading.Timer(timeout_s, conn.cancel_safe)
    timer.daemon = True
    timer.start()
    server_ms = round((timeout_s + SERVER_TIMEOUT_MARGIN_S) * 1000)
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(server_ms)))
            with conn.cursor(name=CURSOR_NAME) as cur:
                cur.itersize = FETCH_ROWS
                cur.execute(statement)
                columns = tuple(column.name for column in cur.description or ())
                rows: list[Mapping[str, Any]] = []
                for row in cur:
                    if len(rows) == row_cap:
                        raise SqlRowCapError(row_cap_message("SQL", row_cap))
                    rows.append(dict(zip(columns, row, strict=True)))
    except psycopg.errors.QueryCanceled:
        raise SqlQueryTimeout(f"the SQL query ran past the {timeout_s:g} s timeout") from None
    except psycopg.Error as error:
        if conn.broken:
            raise SqlConnectionLost("lost the connection to Postgres") from None
        raise SqlQueryError(str(error).strip()) from None
    finally:
        timer.cancel()
    return QueryResult(columns=columns, rows=tuple(rows))
