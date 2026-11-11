"""The query tool on fake executors: deny list, preview, attempts (spec 0012 AC-12 to AC-15).

No store is reached: each executor here is a plain function returning a
`QueryResult` or raising the named error the real executor raises.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from neo4j.graph import Graph, Node
from neo4j.time import DateTime, Duration

from orgos.agent.tools import (
    LIST_CUT,
    PLAIN_VALUES_RULE,
    TEXT_CUT,
    RunTrace,
    StoredResult,
    deny_reason,
    render_cell,
    render_preview,
    run_query,
)
from orgos.eval.handoff import QueryAttempt, Store, StoreOutage
from orgos.graphstore.query import GraphConnectionLost, GraphQueryTimeout, GraphRowCapError
from orgos.sqlstore.query import (
    QueryResult,
    SqlConnectionLost,
    SqlQueryError,
    SqlQueryTimeout,
    SqlRowCapError,
)

MAX_ROWS = 50


def _result(columns: tuple[str, ...], rows: list[tuple[Any, ...]]) -> QueryResult:
    return QueryResult(columns, tuple(dict(zip(columns, row, strict=True)) for row in rows))


def _returning(result: QueryResult) -> Callable[[str], QueryResult]:
    return lambda statement: result


def _raising(error: Exception) -> Callable[[str], QueryResult]:
    def execute(statement: str) -> QueryResult:
        raise error

    return execute


def _trace(execute: Callable[[str], QueryResult], store: Store = "sql") -> RunTrace:
    return RunTrace(store=store, execute=execute, max_rows=MAX_ROWS)


def _clock() -> Callable[[], float]:
    ticks = itertools.count(start=0.0, step=0.25)
    return lambda: next(ticks)


# --- The deny list (AC-13) ---


@pytest.mark.parametrize(
    "statement",
    [
        "LOAD CSV FROM 'file:///x' AS row RETURN row",
        "load  csv with headers from 'x' as r return r",
        "CALL dbms.components() YIELD name RETURN name",
        "CALL DBMS.listConfig() YIELD name RETURN name",
        "USE system SHOW DATABASES",
        "use neo4j MATCH (n) RETURN count(n)",
    ],
)
def test_each_cypher_rule_rejects(statement: str) -> None:
    assert deny_reason(statement, "graph") is not None


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (i:Issue) // use the key\nRETURN i.issueKey AS id",
        "MATCH (i:Issue) /* LOAD CSV dbms. */ RETURN i.issueKey AS id",
        "MATCH (i:Issue) WHERE i.summary = 'we use LOAD CSV and dbms.x' RETURN i.issueKey AS id",
        'MATCH (i:Issue) WHERE i.summary = "use \\" it" RETURN i.issueKey AS id',
        "MATCH (i:Issue) RETURN i.issueKey AS `use`",
        "MATCH (i:Issue) WHERE i.statusName = 'In Use' RETURN count(i) AS value",
        "MATCH (i:Issue) RETURN i.issueKey AS user_key",
    ],
)
def test_a_denied_word_in_a_comment_string_or_name_runs(statement: str) -> None:
    assert deny_reason(statement, "graph") is None


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1 AS value; SELECT 2 AS value",
        "SELECT 1 AS value; DROP TABLE model.issue",
        "select 1;\n\nselect 2;",
    ],
)
def test_two_sql_statements_are_rejected(statement: str) -> None:
    assert deny_reason(statement, "sql") is not None


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) AS value FROM model.issue",
        "SELECT count(*) AS value FROM model.issue;",
        "SELECT 'a;b' AS value",
        "SELECT 1 AS value -- one; two\n",
        "SELECT 1 AS value /* x; y */",
        'SELECT 1 AS "a;b"',
        "SELECT $q$ ; $q$ AS value",
        "SELECT 'it''s; fine' AS value",
        "SELECT 1 AS value WHERE 'use' = 'use'",
    ],
)
def test_one_sql_statement_runs(statement: str) -> None:
    assert deny_reason(statement, "sql") is None


def test_a_rejected_call_is_an_attempt_and_never_reaches_the_store() -> None:
    called: list[str] = []
    trace = _trace(lambda statement: called.append(statement) or _result(("value",), []), "graph")
    text = run_query(trace, "CALL dbms.components() YIELD name RETURN name")
    assert called == []
    assert text.startswith("rejected, no handle:")
    assert trace.attempts == [
        QueryAttempt("CALL dbms.components() YIELD name RETURN name", "rejected", 0.0, 0)
    ]
    assert trace.results == {}


# --- Cells and the preview (AC-14) ---


def test_cells_render_by_value_type() -> None:
    assert render_cell(None) == "null"
    assert render_cell(True) == "true"
    assert render_cell(False) == "false"
    assert render_cell(3600) == "3600"
    assert render_cell(Decimal("2.50")) == "2.50"
    assert render_cell(1.5) == "1.5"
    assert render_cell("CLOUD-1") == "CLOUD-1"
    assert render_cell(["CLOUD-1", "CLOUD-2"]) == '["CLOUD-1", "CLOUD-2"]'
    assert render_cell(("a", None)) == '["a", null]'
    assert render_cell("a|b\nc") == "a\\|b\\nc"


def test_times_are_iso_with_z_for_both_stores() -> None:
    sql_time = dt.datetime(2013, 1, 1, 3, 30, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    graph_time = DateTime(2013, 1, 1, 1, 30, 0, tzinfo=dt.UTC)
    assert render_cell(sql_time) == "2013-01-01T01:30:00Z"
    assert render_cell(graph_time) == "2013-01-01T01:30:00Z"
    assert render_cell([graph_time]) == '["2013-01-01T01:30:00Z"]'
    assert render_cell(dt.date(2013, 1, 1)) == "2013-01-01"
    assert render_cell(Duration(days=1, seconds=3)) == "P1DT3S"


def test_long_text_and_long_lists_are_cut_with_the_removed_count() -> None:
    text = "x" * (TEXT_CUT + 7)
    assert render_cell(text) == "x" * TEXT_CUT + "…[7 chars cut]"
    listed = ["y" * 10] * 60
    full = render_cell(listed[:1])
    assert full == '["yyyyyyyyyy"]'
    cell = render_cell(listed)
    whole = len('["' + '", "'.join(listed) + '"]')
    assert cell.endswith(f"…[{whole - LIST_CUT} chars cut]")
    assert len(cell) == LIST_CUT + len(f"…[{whole - LIST_CUT} chars cut]")


def test_the_preview_shows_the_header_once_and_the_first_rows() -> None:
    rows = tuple((f"CLOUD-{n}", n) for n in range(60))
    text = render_preview("r2", StoredResult(("id", "value"), rows), MAX_ROWS)
    lines = text.splitlines()
    assert lines[0] == "Result r2: 60 rows. Columns: id, value"
    assert lines[1] == "id | value"
    assert lines[2] == "CLOUD-0 | 0"
    assert lines[2 + MAX_ROWS - 1] == "CLOUD-49 | 49"
    assert lines[-1] == "Preview: the first 50 of 60 rows. Decide from counts."
    assert len(lines) == 2 + MAX_ROWS + 1


def test_a_result_with_no_rows_shows_its_header_and_a_count_of_zero() -> None:
    text = render_preview("r1", StoredResult(("id",), ()), MAX_ROWS)
    assert text.splitlines() == ["Result r1: 0 rows. Columns: id", "id"]


def test_a_short_result_says_nothing_about_a_preview() -> None:
    text = render_preview("r1", StoredResult(("value",), ((3,),)), MAX_ROWS)
    assert text.splitlines() == ["Result r1: 1 row. Columns: value", "value", "3"]


# --- Attempts, handles and the registry (AC-12, AC-15) ---


def test_handles_follow_the_call_position_and_failures_keep_no_result() -> None:
    results = iter(
        [
            _result(("value",), [(1,)]),
            SqlQueryError('syntax error at or near "FORM"'),
            _result(("id",), []),
        ]
    )

    def execute(statement: str) -> QueryResult:
        item = next(results)
        if isinstance(item, Exception):
            raise item
        return item

    trace = _trace(execute)
    first = run_query(trace, "SELECT 1 AS value", clock=_clock())
    second = run_query(trace, "SELECT x FORM y", clock=_clock())
    third = run_query(trace, "SELECT issue_key AS id FROM model.issue WHERE false", clock=_clock())
    assert first.startswith("Result r1: 1 row.")
    assert second == 'error, no handle: syntax error at or near "FORM"'
    assert third.startswith("Result r3: 0 rows. Columns: id")
    assert sorted(trace.results) == ["r1", "r3"]
    assert trace.results["r3"] == StoredResult(("id",), ())
    assert [(a.outcome, a.rows, a.seconds) for a in trace.attempts] == [
        ("ok", 1, 0.25),
        ("error", 0, 0.25),
        ("ok", 0, 0.25),
    ]


def test_the_registry_keeps_every_row_not_only_the_preview() -> None:
    rows = [(f"CLOUD-{n}", n) for n in range(60)]
    trace = _trace(_returning(_result(("id", "value"), rows)))
    run_query(trace, "SELECT issue_key AS id, 1 AS value FROM model.issue")
    assert trace.results["r1"].rows == tuple(rows)
    assert trace.attempts[0].rows == 60


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (SqlQueryTimeout("the SQL query ran past the 30 s timeout"), "store_timeout"),
        (GraphQueryTimeout("the Cypher query ran past the 30 s timeout"), "store_timeout"),
        (SqlRowCapError("the SQL query returned more than 200,000 rows"), "row_cap"),
        (GraphRowCapError("the Cypher query returned more than 200,000 rows"), "row_cap"),
    ],
)
def test_a_timeout_and_a_cap_hit_are_labelled(error: Exception, outcome: str) -> None:
    trace = _trace(_raising(error))
    text = run_query(trace, "SELECT 1 AS value", clock=_clock())
    assert text == f"{outcome}, no handle: {error}"
    assert trace.attempts[0].outcome == outcome
    assert trace.attempts[0].rows == 0
    assert trace.results == {}


@pytest.mark.parametrize(
    "error", [SqlConnectionLost("lost the connection to Postgres"), GraphConnectionLost("x")]
)
def test_a_lost_connection_raises_store_outage(error: Exception) -> None:
    with pytest.raises(StoreOutage):
        run_query(_trace(_raising(error)), "SELECT 1 AS value")


def test_two_columns_of_one_name_are_an_error_attempt() -> None:
    result = QueryResult(("id", "id"), ({"id": "B"},))
    trace = _trace(_returning(result))
    text = run_query(trace, "SELECT a.issue_key AS id, b.issue_key AS id FROM x")
    assert text.startswith("error, no handle: two result columns are named id")
    assert trace.attempts[0].outcome == "error"
    assert trace.results == {}


def test_a_node_value_is_refused_naming_the_plain_values_rule() -> None:
    node = Node(Graph(), "e1", 1, ["Issue"], {"issueKey": "CLOUD-1"})
    for cell in (node, [node], {"n": node}):
        trace = _trace(_returning(_result(("i",), [(cell,)])), "graph")
        text = run_query(trace, "MATCH (i:Issue) RETURN i")
        assert text == f"error, no handle: {PLAIN_VALUES_RULE}"
        assert trace.results == {}


def test_the_same_rows_give_the_same_preview_in_both_stores() -> None:
    when = dt.datetime(2013, 1, 1, tzinfo=dt.UTC)
    sql = _trace(_returning(_result(("id", "at"), [("CLOUD-1", when)])), "sql")
    graph_when = DateTime(2013, 1, 1, 0, 0, 0, tzinfo=dt.UTC)
    graph = _trace(_returning(_result(("id", "at"), [("CLOUD-1", graph_when)])), "graph")
    assert run_query(sql, "SELECT 1") == run_query(graph, "RETURN 1")
