"""Scoring a gold pair or agent run, and the summary of all of them (spec 0011 AC-21 to AC-24).

`score` passes the raw answer through `store_answer` and `compare` from
`orgos.eval.compare`, unchanged. A converter refusal (a NaN, an infinite value
or a non number) scores that one pair or run as `malformed` with three false
columns, and the message never holds the value. The per step missing and extra
counts are diagnosis only: they are counted, never listed. The summary splits
every entry by layer, store, question type and data tag as raw counts, with the
headline in full and without the zero guarded questions. Pure.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

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


# The summary (AC-23, AC-24, AC-27, AC-33)

Layer = Literal["gold", "agent", "replay"]
LIMITS = ("requests", "tool_calls", "tokens")
QUESTION_TYPES = ("aggregate", "path_connection", "mixed")
DATA_TAGS = ("real", "synthetic")


@dataclass(frozen=True)
class Entry:
    """One scored gold pair or agent run, with what the summary splits and counts by.

    A planned run with no file, or a pair with no try, is an entry with outcome
    `not_tried`, so nothing leaves the denominator. `limit` names the limit a
    `limit_hit` run reached; `timeouts` and `row_caps` count its attempts with those labels.
    """

    layer: Layer
    version: str
    store: str
    question_id: str
    question_type: str
    data: str
    outcome: ScoredOutcome
    attempts: int = 0
    retries: int = 0
    timeouts: int = 0
    row_caps: int = 0
    limit: str | None = None
    missing: str | None = None


def _cell(entries: Sequence[Entry]) -> dict[str, int]:
    return {
        "correct": sum(1 for entry in entries if entry.outcome == "correct"),
        "total": len(entries),
    }


def _shown(cell: Mapping[str, int]) -> str:
    return f"{cell['correct']}/{cell['total']}"


@dataclass(frozen=True)
class Group:
    """One layer, version and store of the summary, every cell a raw count."""

    layer: Layer
    version: str
    store: str
    entries: tuple[Entry, ...]
    zero_guarded: frozenset[str]

    def record(self) -> dict[str, Any]:
        """The group as stored in a run summary record."""
        kept = [e for e in self.entries if e.question_id not in self.zero_guarded]
        outcomes = Counter(entry.outcome for entry in self.entries)
        per_question: dict[str, dict[str, int]] = {}
        for entry in self.entries:
            counts = per_question.setdefault(
                entry.question_id, {"runs": 0, "attempts": 0, "retries": 0}
            )
            counts["runs"] += 1
            counts["attempts"] += entry.attempts
            counts["retries"] += entry.retries
        return {
            "layer": self.layer,
            "version": self.version,
            "store": self.store,
            "headline": _cell(self.entries),
            "without_zero_guarded": _cell(kept),
            "zero_guarded": sorted(self.zero_guarded),
            "by_type": {
                kind: _cell([e for e in self.entries if e.question_type == kind])
                for kind in QUESTION_TYPES
            },
            "by_data": {
                tag: _cell([e for e in self.entries if e.data == tag]) for tag in DATA_TAGS
            },
            "outcomes": {outcome: outcomes[outcome] for outcome in sorted(outcomes)},
            "discrepancies": outcomes["discrepancy"],
            "attempts_timed_out": sum(entry.timeouts for entry in self.entries),
            "attempts_at_row_cap": sum(entry.row_caps for entry in self.entries),
            "limit_hit": {
                limit: sum(1 for e in self.entries if e.limit == limit) for limit in LIMITS
            },
            "per_question": per_question,
            "partial": self.layer == "agent" and any(e.missing for e in self.entries),
            "missing": sorted(e.missing for e in self.entries if e.missing),
        }

    def lines(self) -> list[str]:
        """The group as printed: the headline in both forms, the counts beside it, the splits."""
        record = self.record()
        zero = ", ".join(record["zero_guarded"]) or "none"
        name = f"{self.layer} {self.version}" if self.layer == "agent" else self.layer
        lines = [
            f"{name} {self.store}: {_shown(record['headline'])} correct"
            f" ({_shown(record['without_zero_guarded'])} without the zero guarded {zero})"
            + (" PARTIAL" if record["partial"] else ""),
            f"  discrepancies {record['discrepancies']}; attempts timed out"
            f" {record['attempts_timed_out']}, at the row cap {record['attempts_at_row_cap']}",
            "  by type: "
            + ", ".join(f"{kind} {_shown(cell)}" for kind, cell in record["by_type"].items()),
            "  by data: "
            + ", ".join(f"{tag} {_shown(cell)}" for tag, cell in record["by_data"].items()),
            "  outcomes: "
            + ", ".join(f"{outcome} {count}" for outcome, count in record["outcomes"].items()),
        ]
        if self.layer != "gold":
            lines.append(
                "  limit_hit by limit: "
                + ", ".join(f"{limit} {count}" for limit, count in record["limit_hit"].items())
            )
            lines.extend(
                f"  {question}: {counts['runs']} runs, {counts['attempts']} attempts,"
                f" {counts['retries']} retries"
                for question, counts in sorted(record["per_question"].items())
                if counts["attempts"]
            )
        if record["partial"]:
            lines.append(f"  partial, missing: {', '.join(record['missing'])}")
        return lines


def groups(entries: Sequence[Entry], zero_guarded: frozenset[str]) -> tuple[Group, ...]:
    """The entries split by layer, version and store, in a fixed order."""
    keys = sorted({(e.layer, e.version, e.store) for e in entries})
    return tuple(
        Group(
            layer,
            version,
            store,
            tuple(e for e in entries if (e.layer, e.version, e.store) == (layer, version, store)),
            zero_guarded,
        )
        for layer, version, store in keys
    )


def versions_statement(found: Sequence[Group]) -> str | None:
    """A plain statement when agent versions disagree on graph versus SQL (AC-33), else None."""
    verdicts: dict[str, str] = {}
    for version in sorted({g.version for g in found if g.layer == "agent"}):
        correct = {
            g.store: g.record()["headline"]["correct"]
            for g in found
            if g.layer == "agent" and g.version == version
        }
        graph, sql = correct.get("graph", 0), correct.get("sql", 0)
        verdicts[version] = "graph ahead" if graph > sql else "SQL ahead" if sql > graph else "tied"
    if len(set(verdicts.values())) <= 1:
        return None
    shown = "; ".join(f"{version} {verdict}" for version, verdict in verdicts.items())
    return f"The agent versions disagree on graph versus SQL: {shown}."


def summary_lines(entries: Sequence[Entry], zero_guarded: frozenset[str]) -> list[str]:
    """The whole summary: every group, then the versions statement when there is one."""
    found = groups(entries, zero_guarded)
    lines = [line for group in found for line in group.lines()]
    statement = versions_statement(found)
    return [*lines, statement] if statement else lines
