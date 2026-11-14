"""Scoring a gold pair or agent run, and the summary of all of them (spec 0011 AC-21 to AC-24).

`score` passes the raw answer through `store_answer` and `compare` from
`orgos.eval.compare`, unchanged. A converter refusal (a NaN, an infinite value
or a non number) scores that one pair or run as `malformed` with three false
columns, and the message never holds the value. The per step missing and extra
counts are diagnosis only: they are counted, never listed. The summary splits
every entry by layer, store, question type and data tag as raw counts, with the
headline in full and without the zero guarded questions. Pure.

The `plain` layer (spec 0013 AC-19 to AC-25) counts `answer_match` instead of
`correct`, prints `source_match`, `correct`, timeouts, row caps and `limit_hit`
beside its headline, says `lower bound` on every line it prints, and stays out
of the versions statement. `plain_statement` sets each store's plain series
against its own noted series of the same version, from run files only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

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

Layer = Literal["gold", "agent", "replay", "plain"]
# A gold pair whose latest try is correct but which is not accepted with its pins
# holding: shown beside the headline, never inside it (your ruling, 2026-11-10).
NOT_REVIEWED: Final = "correct_not_reviewed"
SummaryOutcome = ScoredOutcome | Literal["correct_not_reviewed"]
LIMITS = ("requests", "tool_calls", "tokens")
QUESTION_TYPES = ("aggregate", "path_connection", "mixed")
DATA_TAGS = ("real", "synthetic")


@dataclass(frozen=True)
class Entry:
    """One scored gold pair or agent run, with what the summary splits and counts by.

    A planned run with no file, or a pair with no try, is an entry with outcome
    `not_tried`, so nothing leaves the denominator. `limit` names the limit a
    `limit_hit` run reached; `timeouts` and `row_caps` count its attempts with those labels.
    `answer_match` and `source_match` are the run file's, false for `not_tried`.
    """

    layer: Layer
    version: str
    store: str
    question_id: str
    question_type: str
    data: str
    outcome: SummaryOutcome
    attempts: int = 0
    retries: int = 0
    timeouts: int = 0
    row_caps: int = 0
    limit: str | None = None
    missing: str | None = None
    answer_match: bool = False
    source_match: bool = False


def _cell(entries: Sequence[Entry], layer: Layer = "agent") -> dict[str, int]:
    if layer == "plain":
        return {
            "answer_match": sum(1 for entry in entries if entry.answer_match),
            "total": len(entries),
        }
    return {
        "correct": sum(1 for entry in entries if entry.outcome == "correct"),
        "total": len(entries),
    }


def _shown(cell: Mapping[str, int], key: str = "correct") -> str:
    return f"{cell[key]}/{cell['total']}"


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
        layer = self.layer
        record: dict[str, Any] = {
            "layer": layer,
            "version": self.version,
            "store": self.store,
            "headline": _cell(self.entries, layer),
            "without_zero_guarded": _cell(kept, layer),
            "zero_guarded": sorted(self.zero_guarded),
            "by_type": {
                kind: _cell([e for e in self.entries if e.question_type == kind], layer)
                for kind in QUESTION_TYPES
            },
            "by_data": {
                tag: _cell([e for e in self.entries if e.data == tag], layer) for tag in DATA_TAGS
            },
            "outcomes": {outcome: outcomes[outcome] for outcome in sorted(outcomes)},
            "discrepancies": outcomes["discrepancy"],
            "correct_not_reviewed": outcomes[NOT_REVIEWED],
            "attempts_timed_out": sum(entry.timeouts for entry in self.entries),
            "attempts_at_row_cap": sum(entry.row_caps for entry in self.entries),
            "limit_hit": {
                limit: sum(1 for e in self.entries if e.limit == limit) for limit in LIMITS
            },
            "per_question": per_question,
            "partial": layer in ("agent", "plain") and any(e.missing for e in self.entries),
            "missing": sorted(e.missing for e in self.entries if e.missing),
        }
        if layer == "plain":
            record["source_match"] = sum(1 for e in self.entries if e.source_match)
            record["correct"] = sum(1 for e in self.entries if e.outcome == "correct")
        return record

    def lines(self) -> list[str]:
        """The group as printed: the headline in both forms, the counts beside it, the splits."""
        record = self.record()
        zero = ", ".join(record["zero_guarded"]) or "none"
        if self.layer == "plain":
            return self._plain_lines(record, zero)
        name = f"{self.layer} {self.version}" if self.layer == "agent" else self.layer
        lines = [
            f"{name} {self.store}: {_shown(record['headline'])} correct"
            f" ({_shown(record['without_zero_guarded'])} without the zero guarded {zero})"
            + (
                f"; correct, not reviewed {record['correct_not_reviewed']}"
                if self.layer == "gold"
                else ""
            )
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

    def _plain_lines(self, record: Mapping[str, Any], zero: str) -> list[str]:
        key = "answer_match"
        lines = [
            f"plain {self.version} {self.store}, lower bound: {_shown(record['headline'], key)}"
            f" answer_match ({_shown(record['without_zero_guarded'], key)} without the zero"
            f" guarded {zero})" + (" PARTIAL" if record["partial"] else ""),
            f"  beside it: source_match {record['source_match']}, correct {record['correct']};"
            f" attempts timed out {record['attempts_timed_out']}, at the row cap"
            f" {record['attempts_at_row_cap']}",
            "  by type: "
            + ", ".join(f"{kind} {_shown(cell, key)}" for kind, cell in record["by_type"].items()),
            "  by data: "
            + ", ".join(f"{tag} {_shown(cell, key)}" for tag, cell in record["by_data"].items()),
            "  outcomes: "
            + ", ".join(f"{outcome} {count}" for outcome, count in record["outcomes"].items()),
            "  limit_hit by limit: "
            + ", ".join(f"{limit} {count}" for limit, count in record["limit_hit"].items()),
        ]
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


# The plain statement (spec 0013 AC-23 to AC-25)

STORE_NAMES: Final = {"graph": "graph", "sql": "SQL"}
VERSIONS_NAME: Final = "versions.jsonl"


def _planned(
    files: Sequence[Mapping[str, Any]], question_id: str, store: str, runs: int
) -> list[Mapping[str, Any]]:
    return [
        f
        for f in files
        if f["question"] == question_id and f["store"] == store and 1 <= f["run"] <= runs
    ]


def _not_compared(
    noted: Sequence[Mapping[str, Any]],
    plain: Sequence[Mapping[str, Any]],
    version: str,
    runs: int,
) -> str | None:
    both = [*noted, *plain]
    if any(f["agent_version"] != version for f in both):
        return "other version"
    if len({f["run"] for f in noted}) < runs or len({f["run"] for f in plain}) < runs:
        return "missing files"
    if len({f["load_id"] for f in both}) > 1:
        return "other load"
    return None


def _matched(files: Iterable[Mapping[str, Any]], kept: Iterable[str]) -> int:
    ids = set(kept)
    return sum(1 for f in files if f["question"] in ids and f["answer_match"] is True)


def _store_statement(
    store: str,
    noted_files: Sequence[Mapping[str, Any]],
    plain_files: Sequence[Mapping[str, Any]],
    *,
    version: str,
    question_ids: Sequence[str],
    runs: int,
    zero_guarded: frozenset[str],
) -> dict[str, Any]:
    compared: list[str] = []
    not_compared: dict[str, str] = {}
    noted_kept: list[Mapping[str, Any]] = []
    plain_kept: list[Mapping[str, Any]] = []
    for question_id in sorted(question_ids):
        noted = _planned(noted_files, question_id, store, runs)
        plain = _planned(plain_files, question_id, store, runs)
        reason = _not_compared(noted, plain, version, runs)
        if reason is not None:
            not_compared[question_id] = reason
            continue
        compared.append(question_id)
        noted_kept.extend(noted)
        plain_kept.extend(plain)
    second = [q for q in compared if q not in zero_guarded]

    def counts(kept: Sequence[str]) -> dict[str, dict[str, int]]:
        total = len(kept) * runs
        return {
            "plain": {"answer_match": _matched(plain_kept, kept), "total": total},
            "noted": {"answer_match": _matched(noted_kept, kept), "total": total},
        }

    return {
        "store": store,
        "compared": compared,
        "not_compared": not_compared,
        "full": counts(compared),
        "without_zero_guarded": counts(second),
    }


def _graph_against_sql(plain_groups: Sequence[Group]) -> dict[str, Any]:
    records = {group.store: group.record() for group in plain_groups if group.layer == "plain"}
    headline = {
        store: records[store]["headline"] if store in records else {"answer_match": 0, "total": 0}
        for store in ("graph", "sql")
    }
    partial = any(records[store]["partial"] for store in records) or len(records) < 2
    graph, sql = headline["graph"]["answer_match"], headline["sql"]["answer_match"]
    verdict = None
    if not partial:
        verdict = "graph ahead" if graph > sql else "SQL ahead" if sql > graph else "tied"
    return {"graph": headline["graph"], "sql": headline["sql"], "verdict": verdict}


def plain_statement(
    noted_files: Sequence[Mapping[str, Any]],
    plain_files: Sequence[Mapping[str, Any]],
    plain_groups: Sequence[Group],
    *,
    version: str,
    question_ids: Sequence[str],
    runs: int,
    zero_guarded: frozenset[str],
    versions: Sequence[str],
) -> dict[str, Any]:
    """Plain `answer_match` against the same version's noted `answer_match`, per store.

    Counted over the compared questions times the planned runs, in both forms
    (AC-23); a question is compared only when both series hold every planned
    run file of it in that store, all of this version and on one load ID. The
    graph against SQL line is the full plain headline, with no verdict while a
    plain group is partial (AC-24). `versions` is the versions log in order; a
    later one makes the series stale (AC-25). Reads `question`, `store`, `run`,
    `agent_version`, `load_id` and `answer_match` of each run file, nothing else.
    """
    later = list(versions[versions.index(version) + 1 :]) if version in versions else []
    return {
        "version": version,
        "runs": runs,
        "stores": [
            _store_statement(
                store,
                noted_files,
                plain_files,
                version=version,
                question_ids=question_ids,
                runs=runs,
                zero_guarded=zero_guarded,
            )
            for store in ("graph", "sql")
        ],
        "graph_against_sql": _graph_against_sql(plain_groups),
        "zero_guarded": sorted(zero_guarded),
        "stale": later,
    }


def plain_statement_lines(statement: Mapping[str, Any]) -> list[str]:
    """The plain statement as printed: counts and question ids only, `lower bound` on each line."""
    zero = ", ".join(statement["zero_guarded"]) or "none"
    lines = []
    for part in statement["stores"]:
        full, second = part["full"], part["without_zero_guarded"]
        line = (
            f"plain against noted {STORE_NAMES[part['store']]}, version {statement['version']},"
            f" lower bound: plain {_shown(full['plain'], 'answer_match')}, noted"
            f" {_shown(full['noted'], 'answer_match')} answer_match over"
            f" {len(part['compared'])} compared questions (plain"
            f" {_shown(second['plain'], 'answer_match')}, noted"
            f" {_shown(second['noted'], 'answer_match')} without the zero guarded {zero})"
        )
        if part["not_compared"]:
            line += "; not compared: " + ", ".join(
                f"{question} ({reason})" for question, reason in part["not_compared"].items()
            )
        lines.append(line)
    against = statement["graph_against_sql"]
    verdict = against["verdict"] or "no verdict while a plain series is partial"
    lines.append(
        f"plain graph against SQL, lower bound: graph {_shown(against['graph'], 'answer_match')},"
        f" SQL {_shown(against['sql'], 'answer_match')} answer_match; {verdict}"
    )
    if statement["stale"]:
        lines.append(
            f"the plain series of version {statement['version']} is stale, a lower bound of an"
            f" older version: {VERSIONS_NAME} holds the later {', '.join(statement['stale'])};"
            " nothing is rerun"
        )
    return lines
