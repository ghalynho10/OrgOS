"""The question checker behind `orgos eval check-questions` (spec 0005, AC-7 to AC-12, AC-42).

It enforces every rule of the spec's Checker rules table on your
`eval/questions.toml` and, when present, `eval/amendments.toml`, and lists
every broken rule together, each with its question. Everything else in the
Authoring guide is your judgement and no code checks it. Pure: parsed TOML and
the field lists go in, the broken rules come out.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from orgos.eval import EvalError
from orgos.eval.models import QUESTION_COUNT, QUESTION_IDS, Amendment, Question, read_toml

TYPE_COUNTS = {"aggregate": 7, "path_connection": 7, "mixed": 6}
SHAPES_BY_TYPE = {
    "aggregate": ("number", "map", "ranking"),
    "mixed": ("number", "map", "ranking"),
    "path_connection": ("set", "path", "boolean"),
}
UNITS_BY_SHAPE = {
    "number": ("seconds", "count", "ratio"),
    "map": ("seconds", "count", "ratio"),
    "ranking": ("seconds", "count", "ratio"),
    "set": ("none",),
    "path": ("none",),
    "boolean": ("none",),
}
TRACER_ID = "Q01"
TRACER_TYPE = "aggregate"

USABLE_HEADING = "## Usable fields"
FREE_TEXT_HEADING = "## Free text, not usable"
_BULLET = re.compile(r"^- `([^`]+)`")


@dataclass(frozen=True)
class FieldLists:
    """The two field lists of `eval/data-description.md`, the only ones the checker reads."""

    usable: frozenset[str]
    free_text: frozenset[str]


def _section(markdown: str, heading: str) -> frozenset[str]:
    paths: set[str] = set()
    inside = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
        elif inside and (match := _BULLET.match(line)):
            paths.add(match.group(1))
    return frozenset(paths)


def read_field_lists(markdown: str) -> FieldLists:
    """The backticked paths under `## Usable fields` and `## Free text, not usable` (AC-16)."""
    return FieldLists(_section(markdown, USABLE_HEADING), _section(markdown, FREE_TEXT_HEADING))


def _label(table: object, fallback: str) -> str:
    raw_id = table.get("id") if isinstance(table, Mapping) else None
    return raw_id if isinstance(raw_id, str) and raw_id in QUESTION_IDS else fallback


def _model_errors(error: ValidationError, prefix: tuple[str, ...] = ()) -> Iterator[str]:
    for detail in error.errors():
        loc = tuple(str(part) for part in detail["loc"])
        if loc[: len(prefix)] != prefix:
            continue
        where = ".".join(loc[len(prefix) :]) or "table"
        message = "is missing" if detail["type"] == "missing" else detail["msg"]
        yield f"{where}: {message}"


def _bad_keys(error: ValidationError | None, prefix: tuple[str, ...] = ()) -> frozenset[str]:
    # Top level keys of a question that failed their own check; the rules
    # across keys skip them so one bad value is reported once.
    if error is None:
        return frozenset()
    keys = set()
    for detail in error.errors():
        loc = tuple(str(part) for part in detail["loc"])
        if loc[: len(prefix)] == prefix and len(loc) > len(prefix):
            keys.add(loc[len(prefix)])
    return frozenset(keys)


def _rules_across_keys(table: Mapping[str, Any], bad: frozenset[str]) -> Iterator[str]:
    def ok(*keys: str) -> bool:
        return all(key not in bad and key in table for key in keys)

    if ok("hop_mode"):
        variable = table["hop_mode"] == "variable"
        if variable and "max_hops" not in table:
            yield "max_hops: is required when hop_mode is variable"
        if not variable and "max_hops" in table:
            yield "max_hops: is allowed only when hop_mode is variable"
        if ok("hops") and table["hops"] == 0 and variable:
            yield "hop_mode: must be fixed when hops is 0"
    if ok("type", "hops"):
        if table["type"] == "aggregate" and table["hops"] != 0:
            yield "hops: type aggregate needs hops 0"
        if table["type"] != "aggregate" and table["hops"] == 0:
            yield f"hops: type {table['type']} needs hops 1 or more"
    if ok("type", "shape") and table["shape"] not in SHAPES_BY_TYPE[table["type"]]:
        allowed = ", ".join(SHAPES_BY_TYPE[table["type"]])
        yield f"shape: type {table['type']} allows shape {allowed}"
    if ok("shape", "unit") and table["unit"] not in UNITS_BY_SHAPE[table["shape"]]:
        allowed = " or ".join(UNITS_BY_SHAPE[table["shape"]])
        yield f"unit: shape {table['shape']} allows unit {allowed}"
    if ok("tracer") and table["tracer"] is True:
        if table.get("id") != TRACER_ID:
            yield f"tracer: only {TRACER_ID} may be the tracer"
        elif ok("type") and table["type"] != TRACER_TYPE:
            yield f"tracer: the tracer must be an {TRACER_TYPE}"


def _field_rules(table: Mapping[str, Any], bad: frozenset[str], lists: FieldLists) -> Iterator[str]:
    if "fields" in bad or "fields" not in table:
        return
    for path in table["fields"]:
        if path in lists.free_text:
            yield f"fields: {path} is free text, not usable"
        elif path not in lists.usable:
            yield f"fields: {path} is not in ## Usable fields of eval/data-description.md"


def question_problems(table: object, lists: FieldLists) -> tuple[str, ...]:
    """Every rule one question table breaks on its own (AC-7, AC-10 to AC-12)."""
    error: ValidationError | None = None
    try:
        Question.model_validate(table)
    except ValidationError as caught:
        error = caught
    problems = list(_model_errors(error)) if error is not None else []
    if isinstance(table, Mapping):
        bad = _bad_keys(error)
        problems += _rules_across_keys(table, bad)
        problems += _field_rules(table, bad, lists)
    return tuple(problems)


def _set_problems(tables: Sequence[Mapping[str, Any]], where: str) -> Iterator[str]:
    ids = Counter(table.get("id") for table in tables)
    for question_id in QUESTION_IDS:
        if ids[question_id] == 0:
            yield f"{where}: {question_id} is missing"
        elif ids[question_id] > 1:
            yield f"{where}: {question_id} appears {ids[question_id]} times"
    types = Counter(table.get("type") for table in tables)
    for question_type, expected in TYPE_COUNTS.items():
        if types[question_type] != expected:
            yield f"{where}: {types[question_type]} {question_type}, expected {expected}"
    tracers = sum(1 for table in tables if table.get("tracer") is True)
    if tracers != 1:
        yield f"{where}: {tracers} questions have tracer = true, expected exactly 1"


def _tables(data: Mapping[str, Any], key: str, file: str) -> tuple[tuple[Any, ...], list[str]]:
    problems = [f"{file}: unknown top level key {name}" for name in data if name != key]
    tables = data.get(key, [])
    if not isinstance(tables, list):
        return (), [*problems, f"{file}: {key} must be an array of [[{key}]] tables"]
    return tuple(tables), problems


def _amendment_problems(
    tables: Sequence[Any], question_ids: frozenset[str], lists: FieldLists
) -> Iterator[str]:
    for index, table in enumerate(tables, start=1):
        label = f"amendment {index}"
        error: ValidationError | None = None
        try:
            Amendment.model_validate(table)
        except ValidationError as caught:
            error = caught
        if error is not None:
            for detail in error.errors():
                if detail["loc"] and detail["loc"][0] in ("before", "after"):
                    continue
                where = ".".join(str(part) for part in detail["loc"]) or "table"
                message = "is missing" if detail["type"] == "missing" else detail["msg"]
                yield f"{label} {where}: {message}"
        if not isinstance(table, Mapping):
            continue
        question = table.get("question")
        if isinstance(question, str) and question not in question_ids:
            yield f"{label} question: {question} is not a question in questions.toml"
        before, after = table.get("before"), table.get("after")
        for side, value in (("before", before), ("after", after)):
            if not isinstance(value, Mapping):
                yield f"{label} {side}: must be the full question table"
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            continue
        for problem in _model_errors(error, ("before",)) if error is not None else ():
            yield f"{label} before.{problem}"
        for problem in question_problems(after, lists):
            yield f"{label} after.{problem}"
        if before.get("id") != question:
            yield f"{label} before.id: must be the amended question {question}"
        for key in ("id", "type"):
            if after.get(key) != before.get(key):
                yield f"{label} after.{key}: must keep the {key} of before"


def effective_tables(
    questions: Sequence[Any], amendments: Sequence[Any]
) -> tuple[Mapping[str, Any], ...]:
    """The question tables with each amendment's `after` in place, the last amendment winning."""
    latest = {
        table["question"]: table["after"]
        for table in amendments
        if isinstance(table, Mapping) and isinstance(table.get("after"), Mapping)
    }
    return tuple(
        latest.get(table.get("id"), table) for table in questions if isinstance(table, Mapping)
    )


def check(
    questions: Mapping[str, Any], lists: FieldLists, amendments: Mapping[str, Any] | None = None
) -> tuple[str, ...]:
    """Every broken rule of the Checker rules table, each naming its question; empty means pass."""
    tables, problems = _tables(questions, "question", "questions.toml")
    if len(tables) != QUESTION_COUNT:
        problems.append(f"questions.toml: {len(tables)} [[question]] tables, expected 20")
    for index, table in enumerate(tables, start=1):
        label = _label(table, f"question {index}")
        problems += (f"{label} {problem}" for problem in question_problems(table, lists))
    problems += _set_problems([t for t in tables if isinstance(t, Mapping)], "questions.toml")
    if amendments is not None:
        amended, amendment_file = _tables(amendments, "amendment", "amendments.toml")
        problems += amendment_file
        ids = frozenset(t.get("id") for t in tables if isinstance(t, Mapping))
        problems += _amendment_problems(
            amended, frozenset(i for i in ids if isinstance(i, str)), lists
        )
        if amended:
            problems += _set_problems(effective_tables(tables, amended), "with amendments applied")
    return tuple(problems)


def check_files(questions_path: Path, description_path: Path, amendments_path: Path) -> str:
    """Read the three files, check them, and return the PASS line; raise listing every problem."""
    if not description_path.exists():
        raise EvalError(f"{description_path} not found: run `orgos eval describe` first")
    lists = read_field_lists(description_path.read_text(encoding="utf-8"))
    questions = read_toml(questions_path)
    amendments = read_toml(amendments_path) if amendments_path.exists() else None
    problems = check(questions, lists, amendments)
    if problems:
        noun = "rule" if len(problems) == 1 else "rules"
        listed = "\n".join(f"  {problem}" for problem in problems)
        raise EvalError(f"check-questions FAIL: {len(problems)} broken {noun}\n{listed}")
    amended = len(amendments.get("amendment", [])) if amendments is not None else 0
    return f"check-questions PASS: {QUESTION_COUNT} questions, {amended} amendments"
