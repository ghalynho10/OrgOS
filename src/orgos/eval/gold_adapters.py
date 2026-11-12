"""One pure adapter per question: gold query rows reshaped into `store_answer`'s arguments.

Spec 0011 AC-12. An adapter takes the answer file's result and one result per
source step file, and returns the `value` and `sources` that `store_answer`
takes. It only reshapes: no arithmetic, no filtering, no sorting that changes
meaning, and no filling of a missing key or a zero. Every group, zeros
included, must come from the query itself (checklist item 4). A source step
file returns one column per kind, named after the kind, with one ID per row, so
an empty step still names its kinds (an empty list is kept, AC-26). A pair with
no adapter here is refused.
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
    """One source step: each column is a kind, its values the IDs, in row order."""
    return {kind: column(result, kind) for kind in result.columns}


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


ADAPTERS: Mapping[str, Adapter] = {
    "Q01": q01,
    "Q02": q02,
}
