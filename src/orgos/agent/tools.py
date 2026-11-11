"""The agent's one query tool: deny list, preview and attempts (spec 0012 AC-12 to AC-15).

`run_query` runs one statement through the store's executor (the reader login
or the read only graph, with the one timeout and `STORE_ROW_CAP`), records the
call as a `QueryAttempt`, keeps a successful result's full rows under the
handle `r<n>`, and returns to the model a compact preview. Everything it says
to the model is text: a store error uses a tool call, not a retry. The deny
check, the cell rendering and the preview are pure and serve both stores
alike; `RunTrace` is the one mutable thing, and it lives for one run only.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from neo4j.graph import Node, Path, Relationship
from neo4j.time import Duration

from orgos.eval.handoff import AttemptOutcome, QueryAttempt, Store, StoreOutage
from orgos.graphstore.query import (
    GraphConnectionLost,
    GraphQueryError,
    GraphQueryTimeout,
    GraphRowCapError,
)
from orgos.sqlstore.query import (
    QueryResult,
    SqlConnectionLost,
    SqlQueryError,
    SqlQueryTimeout,
    SqlRowCapError,
)

TOOL_DESCRIPTION = (
    "Run one read only statement. Returns a result handle, the columns, the row count"
    " and a preview of the first rows."
)
TEXT_CUT = 80
LIST_CUT = 400
PLAIN_VALUES_RULE = (
    "return plain values (a key, a name, a number, a date), never whole nodes,"
    " relationships, paths or maps"
)

# Literals and comments are blanked before the deny check, so a word inside
# them never trips it (AC-13). SQL: `--` and `/* */` comments, '' strings,
# "" identifiers, $tag$ bodies. Cypher: `//` and `/* */` comments, '' and ""
# strings with backslash escapes, `backtick` names.
_SQL_HIDDEN = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\$(\w*)\$.*?\$\1\$", re.DOTALL
)
_CYPHER_HIDDEN = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`", re.DOTALL
)
_CYPHER_DENIED = (
    (re.compile(r"\bLOAD\s+CSV\b", re.IGNORECASE), "LOAD CSV"),
    (re.compile(r"\bdbms\.", re.IGNORECASE), "dbms. procedures"),
    (re.compile(r"\bUSE\b", re.IGNORECASE), "USE"),
)

Executor = Callable[[str], QueryResult]


@dataclass(frozen=True)
class StoredResult:
    """One successful call's full result: the columns once, every row as a tuple."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


@dataclass
class RunTrace:
    """The calls of one run: its store and executor, each attempt in order, each kept result.

    The one mutable object of a run; a new one is made for every run, so no
    attempt or handle crosses from one run to the next (AC-6).
    """

    store: Store
    execute: Executor
    max_rows: int
    attempts: list[QueryAttempt] = field(default_factory=list)
    results: dict[str, StoredResult] = field(default_factory=dict)
    # Model responses received, counted by the agent's hook: Pydantic AI 2.61 leaves
    # out of `requests` the response that goes over the token limit.
    requests: int = 0


def blank_hidden(statement: str, store: Store) -> str:
    """`statement` with every string literal, quoted name and comment replaced by a space."""
    pattern = _SQL_HIDDEN if store == "sql" else _CYPHER_HIDDEN
    return pattern.sub(" ", statement)


def deny_reason(statement: str, store: Store) -> str | None:
    """Why the tool refuses `statement` before running it, or None when it may run (AC-13)."""
    visible = blank_hidden(statement, store)
    if store == "sql":
        statements = [part for part in visible.split(";") if part.strip()]
        if len(statements) > 1:
            return "one SQL statement per call; this text holds more than one"
        return None
    for pattern, what in _CYPHER_DENIED:
        if pattern.search(visible):
            return f"{what} is not allowed; only a read of the neo4j database may run"
    return None


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[{len(text) - limit} chars cut]"


def _json_default(value: Any) -> Any:
    plain = _plain(value)
    return plain if plain is not value else str(value)


def _plain(value: Any) -> Any:
    """A driver value as a plain Python value: native times, ISO durations."""
    if isinstance(value, Duration):
        return value.iso_format()
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    return value


def render_cell(value: Any) -> str:
    """One cell as preview text, by value type, the same for both stores (AC-14)."""
    value = _plain(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple | Mapping):
        text = _cut(json.dumps(value, default=_json_default, ensure_ascii=False), LIST_CUT)
    elif isinstance(value, str):
        text = _cut(value, TEXT_CUT).replace("\r", "\\r").replace("\n", "\\n")
    else:
        text = str(value)
    # One row per line and one `|` per column border.
    return text.replace("|", "\\|")


def holds_graph_value(value: Any) -> bool:
    """Whether a cell is, or holds, a node, relationship or path."""
    if isinstance(value, Node | Relationship | Path):
        return True
    if isinstance(value, Mapping):
        return any(holds_graph_value(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(holds_graph_value(item) for item in value)
    return False


def duplicate_columns(columns: tuple[str, ...]) -> tuple[str, ...]:
    """The column names that occur more than once, in first seen order."""
    seen: set[str] = set()
    repeated: list[str] = []
    for column in columns:
        if column in seen and column not in repeated:
            repeated.append(column)
        seen.add(column)
    return tuple(repeated)


def render_preview(handle: str, result: StoredResult, max_rows: int) -> str:
    """The text the model gets for a kept result: handle, count, header once, first rows."""
    total = len(result.rows)
    lines = [
        f"Result {handle}: {total} row{'' if total == 1 else 's'}."
        f" Columns: {', '.join(result.columns)}",
        " | ".join(result.columns),
    ]
    lines.extend(" | ".join(render_cell(cell) for cell in row) for row in result.rows[:max_rows])
    if total > max_rows:
        lines.append(f"Preview: the first {max_rows} of {total} rows. Decide from counts.")
    return "\n".join(lines)


def _failed(
    trace: RunTrace, statement: str, outcome: AttemptOutcome, seconds: float, why: str
) -> str:
    trace.attempts.append(QueryAttempt(statement, outcome, seconds, 0))
    return f"{outcome}, no handle: {why}"


def run_query(
    trace: RunTrace, statement: str, *, clock: Callable[[], float] = time.monotonic
) -> str:
    """Run one statement for the model and return what it sees; record the attempt (AC-12 to AC-15).

    A lost store connection raises `StoreOutage`: the run cannot go on, and the
    runner treats it like a provider outage.
    """
    reason = deny_reason(statement, trace.store)
    if reason is not None:
        return _failed(trace, statement, "rejected", 0.0, reason)
    started = clock()
    try:
        result = trace.execute(statement)
    except (SqlQueryTimeout, GraphQueryTimeout) as error:
        return _failed(trace, statement, "store_timeout", clock() - started, str(error))
    except (SqlRowCapError, GraphRowCapError) as error:
        return _failed(trace, statement, "row_cap", clock() - started, str(error))
    except (SqlConnectionLost, GraphConnectionLost) as error:
        raise StoreOutage(str(error)) from None
    except (SqlQueryError, GraphQueryError) as error:
        return _failed(trace, statement, "error", clock() - started, str(error))
    seconds = clock() - started
    repeated = duplicate_columns(result.columns)
    if repeated:
        why = f"two result columns are named {', '.join(repeated)}; give each column its own name"
        return _failed(trace, statement, "error", seconds, why)
    rows = tuple(tuple(row[column] for column in result.columns) for row in result.rows)
    if any(holds_graph_value(cell) for row in rows for cell in row):
        return _failed(trace, statement, "error", seconds, PLAIN_VALUES_RULE)
    handle = f"r{len(trace.attempts) + 1}"
    stored = StoredResult(columns=result.columns, rows=rows)
    trace.results[handle] = stored
    trace.attempts.append(QueryAttempt(statement, "ok", seconds, len(rows)))
    return render_preview(handle, stored, trace.max_rows)
