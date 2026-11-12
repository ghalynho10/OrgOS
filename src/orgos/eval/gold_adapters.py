"""One pure adapter per question: gold query rows reshaped into `store_answer`'s arguments.

Spec 0011 AC-12. An adapter takes the answer file's result and one result per
source step file, and returns the `value` and `sources` that `store_answer`
takes. It only reshapes: no arithmetic, no filtering, no sorting that changes
meaning, and no filling of a missing key or a zero. Every group, zeros
included, must come from the query itself (checklist item 4). A source step
file returns one column per kind, named after the kind, with one ID per row, so
an empty step still names its kinds (an empty list is kept, AC-26). A pair with
no adapter here is refused.

An empty cell (AC-12, amended 2026-11-12): when the kinds of a step have
different numbers of IDs, as in Q04's step 1 where some issues have no time
entry, a kind's cell is a true NULL (Python `None`) on the rows it has no ID
for. That is the absence of an ID, not a value, so `step()` skips it, and
skipping it is not filtering. An empty string or the text "None" is a value: it
stays an ID and fails loudly as an extra one. The agent's rule (spec 0012
AC-17) still sends the model back on any NULL source cell, because a gold pair
must also match the known evidence exactly and pass hand review.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from orgos.eval import EvalError
from orgos.sqlstore.query import QueryResult

Sources = list[dict[str, list[object]]]
Adapter = Callable[[QueryResult, tuple[QueryResult, ...]], tuple[Any, Sources]]


def column(result: QueryResult, name: str) -> list[Any]:
    """Every value of one column in row order, or an `EvalError` naming the missing column."""
    if name not in result.columns:
        returned = ", ".join(result.columns) or "no columns"
        raise EvalError(f"the query returns no column {name} (it returns {returned})")
    return [row[name] for row in result.rows]


def single(result: QueryResult, name: str) -> Any:
    """The one value of a one row result, or an `EvalError` naming the row count."""
    values = column(result, name)
    if len(values) != 1:
        raise EvalError(f"the query returns {len(values)} rows, not 1")
    return values[0]


def step(result: QueryResult) -> dict[str, list[object]]:
    """One source step: each column is a kind, its values the IDs, in row order.

    A `None` cell is no ID and is skipped; every other value is kept as it comes.
    """
    return {
        kind: [value for value in column(result, kind) if value is not None]
        for kind in result.columns
    }


def steps(results: tuple[QueryResult, ...]) -> Sources:
    """Every source step, in file order."""
    return [step(result) for result in results]


def q01(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q01, a `map` of project to count: one row per project, `project_key` and `issues`."""
    keys = column(answer, "project_key")
    counts = column(answer, "issues")
    return [(f"project:{key}", count) for key, count in zip(keys, counts, strict=True)], steps(
        sources
    )


def q02(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q02, a `number`: one row, `issues`."""
    return single(answer, "issues"), steps(sources)


def q03(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q03, a `number`: one row, `issues`; its stage files are judged by `gold`, not here."""
    return single(answer, "issues"), steps(sources)


def q04(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q04, a `map` of project to seconds: one row per project, `project_key` and `difference`."""
    keys = column(answer, "project_key")
    differences = column(answer, "difference")
    return [(f"project:{key}", gap) for key, gap in zip(keys, differences, strict=True)], steps(
        sources
    )


def q05(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q05, a `map` of person to seconds: one row per author, `person_key` and `seconds`."""
    keys = column(answer, "person_key")
    seconds = column(answer, "seconds")
    return [(f"person:{key}", total) for key, total in zip(keys, seconds, strict=True)], steps(
        sources
    )


def q06(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q06, a `ranking` of issues by seconds: rows in the query's rank order, cut included.

    `issue_key` and `seconds` only; the `project_key` column is for the reader, since a
    ranking item carries an ID and a value and nothing else.
    """
    keys = column(answer, "issue_key")
    seconds = column(answer, "seconds")
    return [(f"issue:{key}", total) for key, total in zip(keys, seconds, strict=True)], steps(
        sources
    )


def q07(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q07, a `ranking` of issues by `difference`: rows in the query's rank order, cut included."""
    keys = column(answer, "issue_key")
    differences = column(answer, "difference")
    return [(f"issue:{key}", gap) for key, gap in zip(keys, differences, strict=True)], steps(
        sources
    )


ADAPTERS: Mapping[str, Adapter] = {
    "Q01": q01,
    "Q02": q02,
    "Q03": q03,
    "Q04": q04,
    "Q05": q05,
    "Q06": q06,
    "Q07": q07,
}
