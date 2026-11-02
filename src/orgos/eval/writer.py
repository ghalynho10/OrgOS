"""The deterministic answer writer and the amendment triggers (spec 0005, AC-29 to AC-33).

The question functions hand their exact results to the builders here, which
type every ID as `kind:id`, sort every ID list in code point order, keep every
number as the integers computed (never reduced or rounded) and build ranking
tie groups from exact values. `render` then writes `eval/answers.json` bytes
that are identical for unchanged code and data. Pure: no IO.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction as Exact
from itertools import pairwise
from typing import Any, Literal

from orgos.eval import EvalError
from orgos.eval.models import (
    Answer,
    AnswerFile,
    BooleanAnswer,
    Fraction,
    IntNumber,
    MapAnswer,
    MapItem,
    Number,
    NumberAnswer,
    PathAnswer,
    PathItem,
    RankingAnswer,
    SetAnswer,
    SourceKind,
    Step,
    TieGroup,
)
from orgos.eval.snapshot import Issue

Direction = Literal["descending", "ascending"]


# Numbers (AC-30)


def count(n: int) -> IntNumber:
    """A count or a sum: `{"int": n}`."""
    return IntNumber.model_validate({"int": n})


def ratio(num: int, den: int) -> Fraction:
    """An average or a ratio, kept as computed: `{"num": a, "den": b}`, a zero `den` included."""
    return Fraction(num=num, den=den)


def exact(number: Number) -> Exact | None:
    """The exact value of a stored number; None for a zero denominator."""
    if isinstance(number, IntNumber):
        return Exact(number.int_)
    return None if number.den == 0 else Exact(number.num, number.den)


# IDs and source steps (AC-29, AC-32)


def typed(kind: str, key: str) -> str:
    """An ID typed as `kind:id`."""
    return f"{kind}:{key}"


def _sorted_once(ids: Iterable[str], what: str) -> tuple[str, ...]:
    ordered = tuple(sorted(ids))
    repeated = sorted({i for i, j in pairwise(ordered) if i == j})
    if repeated:
        raise EvalError(f"{what} lists {repeated} more than once")
    return ordered


def step(ids: Mapping[SourceKind, Iterable[str]]) -> Step:
    """One source step: each kind the reading lists, with its bare IDs sorted."""
    return {kind: _sorted_once(keys, f"a {kind} source step") for kind, keys in ids.items()}


# The six shapes


def number_answer(value: Number, sources: Sequence[Step]) -> NumberAnswer:
    """A `number` answer."""
    return NumberAnswer(shape="number", sources=tuple(sources), value=value)


def map_answer(kind: str, values: Mapping[str, Number], sources: Sequence[Step]) -> MapAnswer:
    """A `map` answer: one item per entity, sorted by its typed id."""
    items = sorted((typed(kind, key), value) for key, value in values.items())
    return MapAnswer(
        shape="map",
        sources=tuple(sources),
        value=tuple(MapItem(id=i, value=v) for i, v in items),
    )


def set_answer(kind: str, keys: Iterable[str], sources: Sequence[Step]) -> SetAnswer:
    """A `set` answer, typed ids sorted."""
    ids = _sorted_once((typed(kind, key) for key in keys), "a set answer")
    return SetAnswer(shape="set", sources=tuple(sources), value=ids)


def boolean_answer(value: bool, sources: Sequence[Step]) -> BooleanAnswer:
    """A `boolean` answer; a "no" has an empty step list (AC-32)."""
    return BooleanAnswer(shape="boolean", sources=tuple(sources), value=value)


def path_answer(paths: Iterable[tuple[Sequence[str], Sequence[Step]]]) -> PathAnswer:
    """A `path` answer: each path's typed ids with one step per hop, sorted by the joined ids."""
    items = sorted(
        ((tuple(ids), tuple(steps)) for ids, steps in paths), key=lambda p: " ".join(p[0])
    )
    return PathAnswer(
        shape="path",
        sources=(),
        value=tuple(PathItem(path=ids, sources=steps) for ids, steps in items),
    )


def tie_groups(
    kind: str, values: Mapping[str, Number], direction: Direction, cut: int
) -> tuple[TieGroup, ...]:
    """The tie groups of a ranking by exact value, cut after `cut` items (AC-31).

    The last group at the cut keeps every tied item, so it may run past the cut.
    """
    if cut < 1:
        raise EvalError(f"a ranking cut must be at least 1, not {cut}")
    groups: dict[Exact, list[tuple[str, Number]]] = {}
    for key, value in values.items():
        at = exact(value)
        if at is None:
            raise EvalError(f"{typed(kind, key)} has a zero denominator and cannot be ranked")
        groups.setdefault(at, []).append((typed(kind, key), value))
    kept: list[TieGroup] = []
    for at in sorted(groups, reverse=direction == "descending"):
        if sum(len(group.ids) for group in kept) >= cut:
            break
        members = groups[at]
        stored = {v.model_dump_json(by_alias=True) for _, v in members}
        if len(stored) > 1:
            raise EvalError(
                f"tied ranking items store their equal value differently: {sorted(stored)}"
            )
        ids = _sorted_once((i for i, _ in members), "a ranking tie group")
        kept.append(TieGroup(value=members[0][1], ids=ids))
    return tuple(kept)


def ranked(groups: Sequence[TieGroup], sources: Sequence[Step]) -> RankingAnswer:
    """A `ranking` answer from its tie groups and the sources of the items they hold."""
    return RankingAnswer(shape="ranking", sources=tuple(sources), value=tuple(groups))


def ranking_answer(
    kind: str,
    values: Mapping[str, Number],
    direction: Direction,
    cut: int,
    sources: Sequence[Step],
) -> RankingAnswer:
    """A `ranking` answer whose sources do not depend on which items the cut returns."""
    return ranked(tie_groups(kind, values, direction, cut), sources)


# Writing (AC-29)


def dumped(model: AnswerFile | Answer) -> Any:
    """The JSON value of an answer or the answer file, as `answers.json` holds it."""
    return model.model_dump(mode="json", by_alias=True)


def render_json(value: Any) -> bytes:
    """Keys sorted, two space indent, UTF-8, one trailing newline."""
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def render(
    answers: Mapping[str, Answer], questions_sha256: str, amendments_sha256: str | None
) -> bytes:
    """The bytes of `eval/answers.json`."""
    answer_file = AnswerFile(
        amendments_sha256=amendments_sha256,
        answers=dict(sorted(answers.items())),
        questions_sha256=questions_sha256,
    )
    return render_json(dumped(answer_file))


# Amendment triggers (AC-33)


def _numbers(answer: Answer) -> Iterable[Number]:
    if isinstance(answer, NumberAnswer):
        yield answer.value
    elif isinstance(answer, MapAnswer):
        yield from (item.value for item in answer.value)
    elif isinstance(answer, RankingAnswer):
        yield from (group.value for group in answer.value)


def is_empty_answer(answer: Answer) -> bool:
    """An empty set, map, ranking or path list, or a boolean "no"."""
    if isinstance(answer, BooleanAnswer):
        return not answer.value
    if isinstance(answer, NumberAnswer):
        return False
    return len(answer.value) == 0


def is_zero_count(answer: Answer) -> bool:
    """A `number` answer of `{"int": 0}`: a valid answer whose sources are `[]`, and no trigger."""
    return (
        isinstance(answer, NumberAnswer)
        and isinstance(answer.value, IntNumber)
        and answer.value.int_ == 0
    )


def answer_triggers(question_id: str, answer: Answer, cut: int | None) -> tuple[str, ...]:
    """The empty answer and tie at the cut trigger lines of one answer."""
    lines: list[str] = []
    if is_empty_answer(answer):
        detail = "the answer is no" if isinstance(answer, BooleanAnswer) else "the answer is empty"
        lines.append(f"{question_id} trigger empty_answer: {detail}")
    zero = sum(1 for number in _numbers(answer) if exact(number) is None)
    if zero:
        lines.append(f"{question_id} trigger empty_answer: {zero} zero denominator(s)")
    if isinstance(answer, RankingAnswer) and cut is not None:
        ranked = sum(len(group.ids) for group in answer.value)
        if ranked > cut:
            last = answer.value[-1]
            lines.append(
                f"{question_id} trigger tie_at_cut: {len(last.ids)} items tie in the last group, "
                f"{ranked} ranked for a cut of {cut}"
            )
    return tuple(lines)


def unfilled_triggers(
    question_id: str, fields: Iterable[str], scope: Sequence[Issue]
) -> tuple[str, ...]:
    """A trigger line per listed field with no filled value on any issue in the scope."""
    return tuple(
        f"{question_id} trigger unfilled_field: {path} has no filled value "
        f"in the {len(scope)} issues of its scope"
        for path in fields
        if not any(issue.values(path) for issue in scope)
    )
