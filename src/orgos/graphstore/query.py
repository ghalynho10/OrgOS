"""The one way an eval command or the agent runs a statement on Neo4j (spec 0011 AC-9).

Each call runs one statement in an explicit read transaction on the `neo4j`
database with the query timeout, iterates its records one at a time, and stops
at `STORE_ROW_CAP` plus one record. Writes are refused by Neo4j while the
database is read only, which the shared preflight checks. The result has the
same shape as the SQL executor's, whole or an error, never partial.
"""

from __future__ import annotations

from typing import Any

from neo4j import READ_ACCESS, Driver, GraphDatabase, NotificationMinimumSeverity
from neo4j.exceptions import DriverError, Neo4jError, ServiceUnavailable, SessionExpired

from orgos.config import STORE_ROW_CAP, Settings
from orgos.graphstore import DATABASE, GraphStoreError
from orgos.sqlstore.query import QueryResult, row_cap_message

# The server's codes for a transaction stopped by its timeout.
TIMEOUT_CODES = frozenset(
    {
        "Neo.ClientError.Transaction.TransactionTimedOut",
        "Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration",
    }
)


class GraphQueryTimeout(GraphStoreError):
    """Raised when a statement runs past the query timeout and the server stops it."""


class GraphRowCapError(GraphStoreError):
    """Raised when a statement returns more than `STORE_ROW_CAP` records."""


class GraphConnectionLost(GraphStoreError):
    """Raised when Neo4j cannot be reached or the connection drops (a store outage)."""


class GraphQueryError(GraphStoreError):
    """Raised when Neo4j refuses a statement: bad syntax, a write, an unknown function."""


def reader_driver(settings: Settings) -> Driver:
    """A driver for the graph; reads go to `neo4j`, which the preflight requires read only."""
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        notifications_min_severity=NotificationMinimumSeverity.WARNING,
    )


def run_cypher(
    driver: Driver,
    statement: str,
    *,
    timeout_s: float,
    row_cap: int = STORE_ROW_CAP,
) -> QueryResult:
    """Run one statement in a read transaction and return its columns and every row."""
    try:
        with (
            driver.session(database=DATABASE, default_access_mode=READ_ACCESS) as session,
            session.begin_transaction(timeout=timeout_s) as tx,
        ):
            result = tx.run(statement)
            columns = tuple(result.keys())
            rows: list[dict[str, Any]] = []
            for record in result:
                if len(rows) == row_cap:
                    raise GraphRowCapError(row_cap_message("Cypher", row_cap))
                rows.append(dict(zip(columns, record.values(), strict=True)))
    except (ServiceUnavailable, SessionExpired):
        raise GraphConnectionLost("lost the connection to Neo4j") from None
    except Neo4jError as error:
        if error.code in TIMEOUT_CODES:
            raise GraphQueryTimeout(
                f"the Cypher query ran past the {timeout_s:g} s timeout"
            ) from None
        raise GraphQueryError(error.message or str(error)) from None
    except DriverError as error:
        raise GraphQueryError(str(error)) from None
    return QueryResult(columns=columns, rows=tuple(rows))
