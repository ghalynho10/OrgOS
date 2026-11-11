"""Scoring one gold pair or agent run against its known answer (spec 0011 AC-21, AC-22).

`score` passes the raw answer through `store_answer` and `compare` from
`orgos.eval.compare`, unchanged. A converter refusal (a NaN, an infinite value
or a non number) scores that one pair or run as `malformed` with three false
columns, and the message never holds the value. The per step missing and extra
counts are diagnosis only: they are counted, never listed. Pure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from orgos.eval import EvalError
from orgos.eval.compare import compare, store_answer
from orgos.eval.handoff import ScoredOutcome

MALFORMED = "a store value is not a finite number, or is not a number"


@dataclass(frozen=True)
class StepCount:
    """One source step: how many known IDs the store left out, and how many it added."""

    missing: int
    extra: int

    def record(self) -> dict[str, int]:
        """The object written to the tries log and run files."""
        return {"missing": self.missing, "extra": self.extra}


@dataclass(frozen=True)
class Scored:
    """The three columns, the outcome, the step counts, and a problem when it is malformed."""

    answer_match: bool
    source_match: bool
    correct: bool
    outcome: ScoredOutcome
    steps: tuple[StepCount, ...]
    problem: str | None = None


def _pairs(steps: Iterable[Mapping[str, Iterable[str]]]) -> list[set[tuple[str, str]]]:
    return [{(kind, str(item)) for kind, ids in step.items() for item in ids} for step in steps]


def step_counts(
    known: Sequence[Mapping[str, Iterable[str]]], store: Sequence[Mapping[str, Iterable[str]]]
) -> tuple[StepCount, ...]:
    """Per step, the known `(kind, id)` pairs the store lacks and the ones it adds.

    A step only one side has counts against an empty step on the other side.
    """
    known_steps, store_steps = _pairs(known), _pairs(store)
    counts = []
    for index in range(max(len(known_steps), len(store_steps))):
        expected = known_steps[index] if index < len(known_steps) else set()
        found = store_steps[index] if index < len(store_steps) else set()
        counts.append(StepCount(missing=len(expected - found), extra=len(found - expected)))
    return tuple(counts)


def failed(outcome: ScoredOutcome) -> Scored:
    """A pair or run that produced no answer to compare: three false columns, no step counts."""
    return Scored(False, False, False, outcome, ())


def score(
    known: Mapping[str, Any],
    shape: str,
    value: Any,
    sources: Iterable[Mapping[str, Iterable[object]]],
) -> Scored:
    """Convert with `store_answer`, judge with `compare`; a converter refusal is `malformed`."""
    try:
        store = store_answer(shape, value, sources)
    except EvalError:
        return Scored(False, False, False, "malformed", (), MALFORMED)
    verdict = compare(known, store)
    return Scored(
        answer_match=verdict.answer_match,
        source_match=verdict.source_match,
        correct=verdict.correct,
        outcome="correct" if verdict.correct else "wrong",
        steps=step_counts(known["sources"], store["sources"]),
    )
