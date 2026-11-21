"""The report fields the page shows, read from `results/findings/report.json` (spec 0016).

Pure: it takes the parsed report and returns frozen rows in the fixed order of
AC-30, reading only the fields named in the spec's *Value sourcing*, so an
unknown key can never reach the page (AC-19). It refuses a report that is
partial, stale or out of line before any row is made (AC-6, AC-31, AC-32).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from orgos.site import SiteError

# The order of the conclusion's sections, never the report's alphabetical keys (AC-30).
TYPES: Final = ("aggregate", "path_connection", "mixed")
STORES: Final = ("sql", "graph")
SCHEMA_VERSION: Final = 1
RULE_PREFIX: Final = "noted, full form, "
NOTE_SCOPE_TYPE: Final = "path_connection"
# The one limitation line the page shows, under the reading note table (AC-12, AC-33).
NOTE_LIMITATION: Final = "G2-6"


@dataclass(frozen=True)
class Count:
    """A count shown as `right/total`."""

    right: int
    total: int

    @property
    def text(self) -> str:
        """The count as the page prints it, for example `18/21`."""
        return f"{self.right}/{self.total}"


@dataclass(frozen=True)
class RuleRow:
    """One question type of the frozen rule's primary family."""

    type: str
    n: int
    m: int
    graph_ahead: int
    sql_ahead: int
    p: str
    smallest_p: str


@dataclass(frozen=True)
class CountsRow:
    """One type and store of the noted full form counts."""

    type: str
    store: str
    correct: Count
    plain: Count
    runs_with_a_retry: int
    attempts_timed_out: int
    attempts_at_row_cap: int
    limit_requests: int
    limit_tokens: int
    limit_tool_calls: int


@dataclass(frozen=True)
class ZeroRow:
    """One type and store, answers right with and without the zero answer questions."""

    type: str
    store: str
    noted: Count
    plain: Count
    noted_without: Count
    plain_without: Count


@dataclass(frozen=True)
class DifferenceRow:
    """One question: graph minus SQL, correct runs of 3."""

    question: str
    type: str
    difference: int


@dataclass(frozen=True)
class NoteRow:
    """One scope and store, answers right with and without the reading note."""

    scope: str
    store: str
    noted: Count
    plain: Count


@dataclass(frozen=True)
class Limitation:
    """One line of the report's `limitations` array."""

    id: str
    text: str
    source: str


@dataclass(frozen=True)
class Evidence:
    """Everything the page reads from the report, in page order."""

    rules: tuple[RuleRow, ...]
    counts: tuple[CountsRow, ...]
    zero: tuple[ZeroRow, ...]
    zero_questions: tuple[str, ...]
    differences: tuple[DifferenceRow, ...]
    notes: tuple[NoteRow, ...]
    note_limitation: Limitation

    def texts(self) -> tuple[str, ...]:
        """Every value the page reads, as text, for the numbers check (AC-9).

        A count is given both as its two fields and as `right/total`, the one
        form the page prints it in.
        """
        values: list[object] = [*self.zero_questions]
        for rule in self.rules:
            values += [rule.n, rule.m, rule.graph_ahead, rule.sql_ahead, rule.p, rule.smallest_p]
        for row in self.counts:
            values += [
                *_count_texts(row.correct, row.plain),
                row.runs_with_a_retry,
                row.attempts_timed_out,
                row.attempts_at_row_cap,
                row.limit_requests,
                row.limit_tokens,
                row.limit_tool_calls,
            ]
        for zero in self.zero:
            values += _count_texts(zero.noted, zero.plain, zero.noted_without, zero.plain_without)
        for difference in self.differences:
            values += [difference.question, difference.difference]
        for note in self.notes:
            values += _count_texts(note.noted, note.plain)
        line = self.note_limitation
        values += [line.id, line.text, line.source]
        return tuple(str(value) for value in values)


def _count_texts(*counts: Count) -> list[object]:
    return [value for count in counts for value in (count.right, count.total, count.text)]


def _field(node: Any, *path: str | int) -> Any:
    for key in path:
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            raise SiteError(f"the findings report has no field {_name(path)}") from None
    return node


def _name(path: tuple[str | int, ...]) -> str:
    return "".join(f"[{key!r}]" for key in path)


def _int(node: Any, *path: str | int) -> int:
    value = _field(node, *path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SiteError(f"the findings report field {_name(path)} is not a whole number")
    return value


def _str(node: Any, *path: str | int) -> str:
    value = _field(node, *path)
    if not isinstance(value, str):
        raise SiteError(f"the findings report field {_name(path)} is not text")
    return value


def _count(node: Any, right: str, *path: str | int) -> Count:
    return Count(_int(node, *path, right), _int(node, *path, "total"))


def refusals(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Why the report cannot be published (AC-6), empty when it can."""
    problems = []
    if report.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version is {report.get('schema_version')!r}, not 1")
    if report.get("partial") is not False:
        problems.append("the report is partial")
    if _field(report, "missing"):
        problems.append("the report has missing runs (missing is not empty)")
    if _field(report, "plain", "stale"):
        problems.append("the plain series is stale (plain.stale is not empty)")
    return tuple(problems)


def _rule_entries(report: Mapping[str, Any]) -> Mapping[str, Any]:
    entries: dict[str, Any] = {}
    tests = _field(report, "tests", "full form")
    if not isinstance(tests, list):
        raise SiteError("the findings report field ['tests']['full form'] is not a list")
    for index in range(len(tests)):
        name = _str(tests, index, "name")
        if _field(tests, index, "role") == "primary" and name.startswith(RULE_PREFIX):
            kind = name.split()[-1]
            if kind in entries:
                raise SiteError(f"the findings report has two primary rule entries for {kind}")
            entries[kind] = tests[index]
    absent = [kind for kind in TYPES if kind not in entries]
    if absent:
        raise SiteError(f"the findings report has no primary rule entry for {', '.join(absent)}")
    return entries


def _beside(report: Mapping[str, Any], kind: str, store: str) -> Any:
    entries = _field(report, "plain", "noted_beside_plain", kind)
    if not isinstance(entries, list):
        raise SiteError(f"the findings report's noted_beside_plain[{kind!r}] is not a list")
    found = [entry for entry in entries if isinstance(entry, dict) and entry.get("store") == store]
    if len(found) != 1:
        raise SiteError(
            f"the findings report's noted_beside_plain[{kind!r}] has {len(found)} entries"
            f" for {store}, not 1"
        )
    return found[0]


def _compared(report: Mapping[str, Any], kind: str) -> tuple[str, ...]:
    by_store = [_field(_beside(report, kind, store), "compared") for store in STORES]
    if by_store[0] != by_store[1] or not isinstance(by_store[0], list):
        raise SiteError(f"the graph and SQL compared lists of {kind} differ (AC-31)")
    return tuple(_str(by_store[0], index) for index in range(len(by_store[0])))


def _differences(rule: Any, kind: str, compared: tuple[str, ...]) -> tuple[DifferenceRow, ...]:
    differences = _field(rule, "differences")
    if not isinstance(differences, list) or len(differences) != len(compared):
        raise SiteError(f"{kind}: compared and differences differ in length (AC-31)")
    return tuple(
        DifferenceRow(question, kind, _int(differences, index))
        for index, question in enumerate(compared)
    )


def _zero_questions(report: Mapping[str, Any]) -> tuple[str, ...]:
    questions = _field(report, "zero_answer_questions")
    if not isinstance(questions, list) or len(questions) != 2:
        raise SiteError("zero_answer_questions does not hold exactly two entries (AC-32)")
    return tuple(_str(questions, index) for index in range(2))


def _note_limitation(report: Mapping[str, Any]) -> Limitation:
    """The `G2-6` entry only; the rest of the list is reached through the K12 link (AC-12)."""
    lines = _field(report, "limitations")
    if not isinstance(lines, list):
        raise SiteError("the findings report field ['limitations'] is not a list")
    for index, line in enumerate(lines):
        if isinstance(line, dict) and line.get("id") == NOTE_LIMITATION:
            return Limitation(
                NOTE_LIMITATION, _str(lines, index, "text"), _str(lines, index, "source")
            )
    raise SiteError(f"the findings report has no limitation {NOTE_LIMITATION} (AC-33)")


def evidence(report: Mapping[str, Any]) -> Evidence:
    """Read the page's fields from the report, refusing one that cannot be published."""
    problems = refusals(report)
    if problems:
        raise SiteError("the findings report cannot be published: " + "; ".join(problems))
    rules = _rule_entries(report)
    compared = {kind: _compared(report, kind) for kind in TYPES}
    differences = tuple(
        row for kind in TYPES for row in _differences(rules[kind], kind, compared[kind])
    )
    zero_questions = _zero_questions(report)
    tables = _field(report, "tables", "full form")
    counts = []
    zero = []
    for kind in TYPES:
        for store in STORES:
            agent = _field(tables, store, kind, "agent")
            beside = _beside(report, kind, store)
            counts.append(
                CountsRow(
                    kind,
                    store,
                    _count(agent, "correct"),
                    _count(beside, "answer_match", "full", "plain"),
                    _int(agent, "runs_with_a_retry"),
                    _int(agent, "attempts_timed_out"),
                    _int(agent, "attempts_at_row_cap"),
                    _int(agent, "limit_hit", "requests"),
                    _int(agent, "limit_hit", "tokens"),
                    _int(agent, "limit_hit", "tool_calls"),
                )
            )
            zero.append(
                ZeroRow(
                    kind,
                    store,
                    _count(beside, "answer_match", "full", "noted"),
                    _count(beside, "answer_match", "full", "plain"),
                    _count(beside, "answer_match", "without_zero_guarded", "noted"),
                    _count(beside, "answer_match", "without_zero_guarded", "plain"),
                )
            )
    drop = _field(report, "plain", "drop")
    notes = [
        NoteRow(
            "all",
            store,
            _count(drop, "answer_match", store, "noted"),
            _count(drop, "answer_match", store, "plain"),
        )
        for store in STORES
    ]
    notes += [
        NoteRow(
            NOTE_SCOPE_TYPE,
            store,
            _count(_beside(report, NOTE_SCOPE_TYPE, store), "answer_match", "full", "noted"),
            _count(_beside(report, NOTE_SCOPE_TYPE, store), "answer_match", "full", "plain"),
        )
        for store in STORES
    ]
    return Evidence(
        rules=tuple(
            RuleRow(
                kind,
                _int(rules[kind], "n"),
                _int(rules[kind], "m"),
                _int(rules[kind], "graph_ahead"),
                _int(rules[kind], "sql_ahead"),
                _str(rules[kind], "p"),
                _str(rules[kind], "smallest_p"),
            )
            for kind in TYPES
        ),
        counts=tuple(counts),
        zero=tuple(zero),
        zero_questions=zero_questions,
        differences=differences,
        notes=tuple(notes),
        note_limitation=_note_limitation(report),
    )
