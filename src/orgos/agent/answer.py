"""The answer: what the model points at, the checks before a run ends, `assemble` (spec 0012).

The model never types a value or an ID. `final_answer` names the result that
holds the answer, the kind of its IDs and, per source step, the result column
that holds each kind's IDs (AC-16). `answer_problems` checks that pointer
against the stored results before the run may end (AC-17), and `assemble`
copies the value and the sources out of the full stored rows into the form
`store_answer` takes (AC-18, AC-19). Both are pure; neither sorts, filters,
sums or fills. `layout_problems` and `layout_send_back` hold the soft layout
check of spec 0015 (AC-8, AC-9): a wrong evidence layout costs a send back,
never the answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orgos.agent.tools import StoredResult
from orgos.eval import EvalError
from orgos.eval.compare import to_number
from orgos.eval.handoff import SourceStep
from orgos.eval.models import ID_KINDS, Shape

ANSWER_TOOL = "final_answer"
ANSWER_DESCRIPTION = (
    "Give the answer by pointing at results: the result that holds the answer, the kind of"
    " its IDs, and for each source step the result column that holds each kind's IDs."
    " Never type an answer value or an ID."
)
SHAPES: tuple[Shape, ...] = ("number", "boolean", "map", "ranking", "set", "path")
SOURCE_KINDS = (*ID_KINDS, "parent")
# A question's evidence layout: its source steps in order, each the kinds it holds, sorted.
Layout = tuple[tuple[str, ...], ...]
# The columns each shape needs in `answer_result` (AC-17).
SHAPE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "number": ("value",),
    "boolean": ("value",),
    "map": ("id", "value"),
    "ranking": ("id", "value"),
    "set": ("id",),
    "path": ("path",),
}
SINGLE_ROW = frozenset({"number", "boolean"})
MIXED_KINDS = frozenset({"map", "ranking", "set"})
KIND_COLUMN = "kind"


class SourceRef(BaseModel):
    """Where one kind's IDs are: a result handle and one of its columns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: str = Field(description="a result handle, for example r4")
    column: str = Field(description="the column of that result that holds the IDs")


class FinalAnswer(BaseModel):
    """The answer as the model gives it: pointers into stored results, never values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shape: str = Field(description="number, boolean, map, ranking, set or path")
    answer_result: str = Field(description="the handle of the result that holds the answer")
    id_kind: str | None = Field(
        None,
        description="the kind of the answer's IDs: issue, project, person, time_entry or"
        " issue_link; leave it out for number and boolean, or when the result has a column kind",
    )
    sources: list[dict[str, SourceRef]] = Field(
        description="the source steps; each maps a kind (issue, project, person, time_entry,"
        " issue_link or parent) to the result and column that hold its IDs"
    )


def is_id(value: Any) -> bool:
    """Whether a cell can be an ID: a `str` or an `int`, never a `bool`."""
    return isinstance(value, str | int) and not isinstance(value, bool)


def _column(result: StoredResult, column: str) -> tuple[Any, ...]:
    index = result.columns.index(column)
    return tuple(row[index] for row in result.rows)


def _hop_item_ok(item: Any) -> bool:
    return is_id(item) or (isinstance(item, list | tuple) and all(is_id(i) for i in item))


def _value_problems(answer: FinalAnswer, result: StoredResult) -> list[str]:
    handle, shape = answer.answer_result, answer.shape
    if shape in SINGLE_ROW:
        if len(result.rows) != 1:
            return [f"a {shape} answer needs exactly 1 row in {handle}, it has {len(result.rows)}"]
        cell = _column(result, "value")[0]
        if shape == "boolean" and not isinstance(cell, bool):
            return [f"the value in {handle} is not true or false"]
        return []
    problems = []
    if answer.id_kind is None:
        if shape not in MIXED_KINDS or KIND_COLUMN not in result.columns:
            problems.append(f"a {shape} answer needs id_kind, or a column kind in {handle}")
        else:
            kinds = _column(result, KIND_COLUMN)
            if None in kinds:
                problems.append(f"column kind of {handle} holds NULL")
            bad = sorted({str(kind) for kind in kinds if kind is not None and kind not in ID_KINDS})
            if bad:
                problems.append(
                    f"column kind of {handle} holds {', '.join(bad)}; each must be one of"
                    f" {', '.join(ID_KINDS)}"
                )
    elif answer.id_kind not in ID_KINDS:
        problems.append(f"id_kind {answer.id_kind!r} is not one of {', '.join(ID_KINDS)}")
    if shape == "path":
        paths = _column(result, "path")
        if any(path is None for path in paths):
            problems.append(f"column path of {handle} holds NULL")
        elif not all(isinstance(p, list | tuple) and all(is_id(i) for i in p) for p in paths):
            problems.append(f"each path in {handle} must be a list of IDs (text or whole numbers)")
    else:
        ids = _column(result, "id")
        if None in ids:
            problems.append(f"column id of {handle} holds NULL")
        elif not all(is_id(cell) for cell in ids):
            problems.append(f"column id of {handle} must hold IDs (text or whole numbers)")
    return problems


def _source_problems(answer: FinalAnswer, results: Mapping[str, StoredResult]) -> list[str]:
    problems = []
    is_path = answer.shape == "path"
    if is_path and len(answer.sources) != 1:
        problems.append(f"a path answer has exactly 1 source step, not {len(answer.sources)}")
    for number, step in enumerate(answer.sources, start=1):
        if not step:
            problems.append(f"source step {number} names no kind")
        for kind, ref in step.items():
            where = f"source step {number}, {kind}"
            if kind not in SOURCE_KINDS:
                problems.append(f"{where}: the kind is not one of {', '.join(SOURCE_KINDS)}")
                continue
            if is_path and ref.result != answer.answer_result:
                problems.append(f"{where}: a path's source columns are in {answer.answer_result}")
                continue
            result = results.get(ref.result)
            if result is None:
                problems.append(f"{where}: there is no result {ref.result}")
                continue
            if ref.column not in result.columns:
                problems.append(f"{where}: {ref.result} has no column {ref.column}")
                continue
            cells = _column(result, ref.column)
            if None in cells:
                problems.append(f"{where}: column {ref.column} of {ref.result} holds NULL")
            elif not is_path and not all(is_id(cell) for cell in cells):
                problems.append(f"{where}: column {ref.column} of {ref.result} must hold IDs")
            elif is_path:
                problems.extend(_hop_problems(where, ref, result))
    return problems


def _hop_problems(where: str, ref: SourceRef, result: StoredResult) -> list[str]:
    paths, items = _column(result, "path"), _column(result, ref.column)
    for row, (path, hops) in enumerate(zip(paths, items, strict=True), start=1):
        if not isinstance(hops, list | tuple) or not all(_hop_item_ok(item) for item in hops):
            return [f"{where}: row {row} must hold a list with one ID or ID list per hop"]
        if isinstance(path, list | tuple) and len(hops) != len(path) - 1:
            return [
                f"{where}: row {row} has {len(hops)} hop items for a path of {len(path)},"
                f" it needs {len(path) - 1}"
            ]
    return []


def answer_problems(
    answer: FinalAnswer, results: Mapping[str, StoredResult], expected_shape: Shape | None
) -> tuple[str, ...]:
    """Every reason `final_answer` cannot end the run yet; empty when it passes (AC-17).

    `expected_shape` is the question's shape on a scored run, None in plain
    question mode, where the model states the shape itself.
    """
    if answer.shape not in SHAPES:
        return (f"shape {answer.shape!r} is not one of {', '.join(SHAPES)}",)
    problems = []
    if expected_shape is not None and answer.shape != expected_shape:
        problems.append(f"the question asks for a {expected_shape} answer, not a {answer.shape}")
    result = results.get(answer.answer_result)
    missing: list[str] = []
    if result is None:
        problems.append(f"there is no result {answer.answer_result}")
    else:
        missing = [c for c in SHAPE_COLUMNS[answer.shape] if c not in result.columns]
        if missing:
            problems.append(
                f"a {answer.shape} answer needs column {' and '.join(missing)} in"
                f" {answer.answer_result}, which has {', '.join(result.columns) or 'no columns'}"
            )
        else:
            problems.extend(_value_problems(answer, result))
    # A path's source columns line up with its path column, so they are checked only once it exists.
    if answer.shape != "path" or (result is not None and not missing):
        problems.extend(_source_problems(answer, results))
    return tuple(problems)


def _typed(kind: str, value: Any) -> str:
    return f"{kind}:{value}"


def _row_kinds(answer: FinalAnswer, result: StoredResult) -> tuple[str, ...]:
    if answer.id_kind is not None:
        return (answer.id_kind,) * len(result.rows)
    return tuple(str(kind) for kind in _column(result, KIND_COLUMN))


def _is_empty(shape: str, value: Any) -> bool:
    # The known answers' rule: an empty list, a "no", or a count of 0
    # (`is_empty_answer`, `is_zero_count` in orgos.eval.writer).
    if shape == "boolean":
        return value is False
    if shape == "number":
        try:
            return to_number(value) == {"int": 0}
        except EvalError:
            return False
    return len(value) == 0


def _hop_ids(item: Any) -> tuple[str, ...]:
    return tuple(str(i) for i in item) if isinstance(item, list | tuple) else (str(item),)


def _answer_value(answer: FinalAnswer, results: Mapping[str, StoredResult]) -> Any:
    result = results[answer.answer_result]
    shape = answer.shape
    if shape == "path":
        kinds = _row_kinds(answer, result)
        step = answer.sources[0]
        columns = {kind: _column(result, ref.column) for kind, ref in step.items()}
        paths = []
        for row, path in enumerate(_column(result, "path")):
            hops = tuple(
                {kind: _hop_ids(cells[row][hop]) for kind, cells in columns.items()}
                for hop in range(len(path) - 1)
            )
            paths.append((tuple(_typed(kinds[row], node) for node in path), hops))
        return paths
    if shape in SINGLE_ROW:
        return _column(result, "value")[0]
    kinds = _row_kinds(answer, result)
    ids = tuple(_typed(kind, cell) for kind, cell in zip(kinds, _column(result, "id"), strict=True))
    if shape == "set":
        return list(ids)
    return list(zip(ids, _column(result, "value"), strict=True))


def assemble(
    answer: FinalAnswer, results: Mapping[str, StoredResult]
) -> tuple[Any, tuple[SourceStep, ...]]:
    """The value and sources of a checked `final_answer`, copied from the full stored rows.

    Number and boolean: the single cell. Map and ranking: `(kind:id, cell)`
    pairs in stored row order. Set: typed IDs in row order. Path: `(typed path,
    one step per hop)` pairs, with the top level sources empty. Source steps
    hold bare IDs under their kind; an empty value whose every source list is
    empty has no steps at all, while a non empty value keeps an empty list.
    """
    shape = answer.shape
    value = _answer_value(answer, results)
    if shape == "path":
        return value, ()
    sources = tuple(
        {
            kind: tuple(str(cell) for cell in _column(results[ref.result], ref.column))
            for kind, ref in step.items()
        }
        for step in answer.sources
    )
    if _is_empty(shape, value) and all(not ids for step in sources for ids in step.values()):
        return value, ()
    return value, sources


def _steps(count: int) -> str:
    return f"{count} step" if count == 1 else f"{count} steps"


def layout_problems(
    answer: FinalAnswer, results: Mapping[str, StoredResult], layout: Layout | None
) -> tuple[str, ...]:
    """Where a `final_answer` that `answer_problems` passed differs from its layout (AC-8).

    Empty when there is no layout, or when the value is empty, zero or false
    (the value half of `assemble`'s rule), sources or not. Otherwise a reason
    for a wrong step count, and one for each step, up to the shorter of the
    two, whose kinds differ from the layout's. Each reason names counts and
    kinds only, never a column, a record or how to find one.
    """
    if layout is None or _is_empty(answer.shape, _answer_value(answer, results)):
        return ()
    problems = []
    if len(answer.sources) != len(layout):
        problems.append(
            f"the layout asks for {_steps(len(layout))}, the answer has {len(answer.sources)}"
        )
    for number, (step, kinds) in enumerate(zip(answer.sources, layout, strict=False), start=1):
        if set(step) != set(kinds):
            problems.append(
                f"step {number} holds {', '.join(sorted(step))}; the layout asks for"
                f" {', '.join(kinds)}"
            )
    return tuple(problems)


def layout_send_back(
    answer: FinalAnswer,
    results: Mapping[str, StoredResult],
    layout: Layout | None,
    retry: int,
    max_retries: int,
) -> tuple[str, ...]:
    """The reasons to send `answer` back for its layout; empty to accept it as given (AC-9).

    `retry` is the output retries used so far. Once none is left the answer is
    accepted with whatever layout it has, so a layout problem alone never ends
    a run.
    """
    if retry >= max_retries:
        return ()
    return layout_problems(answer, results, layout)
