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

A path answer (AC-12, amended 2026-11-12 for feature 15): the answer file
returns one row per path, a `path` column with the path's IDs in order and one
list column per kind with one item per hop, the convention the agent uses
(spec 0012 AC-19). `path_rows()` turns each row into the typed path and one
step per hop; the top level sources stay empty, so a path pair has no sources
file. It refuses, naming columns and lengths and never a value, a missing or
NULL path, a NULL or non list kind cell, a kind list that is not one item
shorter than its path, and a column that is not a kind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, get_args

from orgos.eval import EvalError
from orgos.eval.models import SourceKind
from orgos.sqlstore.query import QueryResult

Sources = list[dict[str, list[object]]]
Adapter = Callable[[QueryResult, tuple[QueryResult, ...]], tuple[Any, Sources]]
KINDS: frozenset[str] = frozenset(get_args(SourceKind))


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


def _hop(item: object) -> list[object]:
    # One hop's IDs of one kind: a single ID is wrapped, a NULL is no ID. A list
    # nested inside the hop's list is kept as one item on purpose, not flattened:
    # the adapter only reshapes (AC-12), so a malformed hop reaches compare as is.
    if item is None:
        return []
    if isinstance(item, list | tuple):
        return [value for value in item if value is not None]
    return [item]


def path_rows(answer: QueryResult, node: str) -> list[tuple[list[str], Sources]]:
    """Each row as `(typed path, one step per hop)`, in row order; `node` is the path's kind."""
    if "path" not in answer.columns:
        returned = ", ".join(answer.columns) or "no columns"
        raise EvalError(f"the query returns no column path (it returns {returned})")
    kinds = [name for name in answer.columns if name != "path"]
    strays = [name for name in kinds if name not in KINDS]
    if strays:
        raise EvalError(
            f"columns {', '.join(strays)} are neither path nor a kind ({', '.join(sorted(KINDS))})"
        )
    paths = []
    for number, row in enumerate(answer.rows, start=1):
        path = row["path"]
        if not isinstance(path, list | tuple):
            raise EvalError(f"row {number}: column path is NULL or not a list")
        for kind in kinds:
            cell = row[kind]
            if not isinstance(cell, list | tuple):
                raise EvalError(f"row {number}: column {kind} is NULL or not a list")
            if len(cell) != len(path) - 1:
                raise EvalError(
                    f"row {number}: column {kind} holds {len(cell)} item(s) for a path of"
                    f" {len(path)} IDs, it needs {len(path) - 1}"
                )
        hops = [{kind: _hop(row[kind][hop]) for kind in kinds} for hop in range(len(path) - 1)]
        paths.append(([f"{node}:{key}" for key in path], hops))
    return paths


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


def q08(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q08, a `path` of issues: one row per chain, each hop listing `issue_link` and `issue`."""
    return path_rows(answer, "issue"), []


def q09(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q09, a `path` of issues: a row per chain and direction, each hop `issue_link` and `issue`."""
    return path_rows(answer, "issue"), []


def q10(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q10, a `set` of people: one row per source assignee, `person_key`."""
    return [f"person:{key}" for key in column(answer, "person_key")], steps(sources)


def q11(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q11, a `set` of people and projects: one row per member, `kind` and `id`."""
    kinds = column(answer, "kind")
    ids = column(answer, "id")
    return [f"{kind}:{key}" for kind, key in zip(kinds, ids, strict=True)], steps(sources)


def q12(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q12, a `set` of parent issues: one row per qualifying parent, `issue_key`."""
    return [f"issue:{key}" for key in column(answer, "issue_key")], steps(sources)


def q13(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q13, a `set` of sub-tasks: one row per direct child of the parent, `issue_key`."""
    return [f"issue:{key}" for key in column(answer, "issue_key")], steps(sources)


def q14(answer: QueryResult, sources: tuple[QueryResult, ...]) -> tuple[Any, Sources]:
    """Q14, a `boolean`: one row, `resolved`."""
    return single(answer, "resolved"), steps(sources)


ADAPTERS: Mapping[str, Adapter] = {
    "Q01": q01,
    "Q02": q02,
    "Q03": q03,
    "Q04": q04,
    "Q05": q05,
    "Q06": q06,
    "Q07": q07,
    "Q08": q08,
    "Q09": q09,
    "Q10": q10,
    "Q11": q11,
    "Q12": q12,
    "Q13": q13,
    "Q14": q14,
}
