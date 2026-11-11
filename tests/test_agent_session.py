"""The session judge and the catalog assembly of the agent (spec 0012 AC-8, AC-11).

Pure parts only: the reads themselves are covered by the integration tests.
"""

from __future__ import annotations

import secrets
from typing import Any

import pytest

from orgos.agent.describe_sql import index_columns, tables_from_catalog
from orgos.agent.errors import AgentError
from orgos.agent.session import (
    GraphSession,
    SqlSession,
    check_sessions,
    session_problems,
)
from tests.test_agent_describe import SQL_TABLES

LOGIN = f"reader_{secrets.token_hex(8)}"
GOOD_SQL = SqlSession(login=LOGIN, timezone="UTC")
GOOD_GRAPH = GraphSession(access="read-only", timezone="Z")


def test_good_sessions_pass() -> None:
    assert session_problems(GOOD_SQL, GOOD_GRAPH, reader_login=LOGIN) == ()
    check_sessions(GOOD_SQL, GOOD_GRAPH, reader_login=LOGIN)


@pytest.mark.parametrize(
    ("sql", "graph", "check"),
    [
        (SqlSession(f"owner_{secrets.token_hex(8)}", "UTC"), GOOD_GRAPH, "SQL login"),
        (SqlSession(LOGIN, "America/St_Johns"), GOOD_GRAPH, "SQL zone"),
        (GOOD_SQL, GraphSession("read-write", "Z"), "graph access"),
        (GOOD_SQL, GraphSession(None, "Z"), "graph access"),
        (GOOD_SQL, GraphSession("read-only", "+01:00"), "graph zone"),
        (GOOD_SQL, GraphSession("read-only", None), "graph zone"),
    ],
)
def test_each_mismatch_refuses_naming_its_check(
    sql: SqlSession, graph: GraphSession, check: str
) -> None:
    problems = session_problems(sql, graph, reader_login=LOGIN)
    assert len(problems) == 1
    assert problems[0].startswith(f"{check}:")
    with pytest.raises(AgentError, match=check):
        check_sessions(sql, graph, reader_login=LOGIN)


def test_every_failed_check_is_named_at_once_and_no_login_is_printed() -> None:
    wrong = f"owner_{secrets.token_hex(8)}"
    with pytest.raises(AgentError) as error:
        check_sessions(
            SqlSession(wrong, "Europe/Paris"),
            GraphSession("read-write", None),
            reader_login=LOGIN,
        )
    message = str(error.value)
    for check in ("SQL login", "SQL zone", "graph access", "graph zone"):
        assert check in message
    assert wrong not in message
    assert LOGIN not in message
    assert "ORGOS_PG_READER_USER" in message


# --- The catalog rows become the tables the renderer takes (AC-8) ---


def _catalog_rows() -> tuple[tuple[dict[str, Any], ...], ...]:
    columns, keys, indexes = [], [], []
    for table in SQL_TABLES:
        columns.extend(
            {
                "table_name": table.name,
                "column_name": column.name,
                "data_type": column.data_type,
                "not_null": column.not_null,
            }
            for column in table.columns
        )
        if table.primary_key:
            keys.append({"table_name": table.name, "kind": "p", "columns": list(table.primary_key)})
        keys.extend(
            {
                "table_name": table.name,
                "kind": "f",
                "columns": list(key.columns),
                "references_table": key.table,
                "references_columns": list(key.references),
            }
            for key in table.foreign_keys
        )
        indexes.extend(
            {
                "table_name": table.name,
                "definition": f"CREATE INDEX i{n} ON model.{table.name}"
                f" USING btree ({', '.join(index)})",
            }
            for n, index in enumerate(table.indexes)
        )
    return tuple(columns), tuple(keys), tuple(indexes)


def test_the_catalog_rows_give_back_the_model_tables() -> None:
    tables = tables_from_catalog(*_catalog_rows())
    assert tables == tuple(sorted(SQL_TABLES, key=lambda table: table.name))


@pytest.mark.parametrize(
    ("definition", "columns"),
    [
        ("CREATE UNIQUE INDEX issue_pkey ON model.issue USING btree (issue_key)", ("issue_key",)),
        (
            'CREATE INDEX x ON model.fixed_in USING btree (issue_key, "version_id")',
            ("issue_key", "version_id"),
        ),
    ],
)
def test_index_columns_reads_a_plain_definition(definition: str, columns: tuple[str, ...]) -> None:
    assert index_columns(definition) == columns


def test_an_index_that_is_not_plain_stops_the_read() -> None:
    with pytest.raises(AgentError, match="cannot read the columns"):
        index_columns("CREATE INDEX x ON model.issue USING btree (lower(status_name))")
