"""The free text split: which field paths a question may cite (spec 0005, AC-16, AC-17).

`orgos eval describe` walks the corpus issues once, folds every value it sees
into one summary per path, and classes each path as usable (structured) or
free text by the rules fixed in AC-17 before any classification ran. Each sub
rule is its own function here so its test names it. Pure: decoded documents go
in, the two lists come out.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from orgos.eval import EvalError

CATEGORICAL_LIMIT = 50
TEXT_LIMIT = 100

TIME_ENTRY_PAIR = "changelog.items[field=timespent].from/to"
HISTORY_MEMBERS = (
    "changelog.histories[].author.key",
    "changelog.histories[].created",
    "changelog.histories[].id",
)
ALWAYS_USABLE = (*HISTORY_MEMBERS, TIME_ENTRY_PAIR)

PROSE_FIELDS = ("fields.description", "fields.environment", "fields.summary")
PROSE_SUBTREES = ("fields.comment", "fields.comments")
PROSE_ITEMS = ("description", "environment", "summary", "comment")

RAW_SIDES = ("from", "to")
DISPLAY_SIDES = ("fromString", "toString")

# `YYYY-MM-DD`, optionally followed by `Thh:mm:ss`, a fraction, and an offset.
DATE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:?[0-9]{2}))?"
)
# What a path may not hold to be written as one backticked bullet the checker reads.
_UNWRITABLE = re.compile(r"`")


@dataclass(frozen=True)
class PathSummary:
    """What the walk saw at one path, over every non empty value.

    `kinds` holds `number`, `boolean`, `string`, `datetime` or another type
    name; `distinct` keeps the distinct strings up to one past the categorical
    limit, or all of them for a path the caller asked to keep.
    """

    kinds: frozenset[str]
    all_dates: bool
    guarded: bool
    distinct: frozenset[str]


@dataclass(frozen=True)
class Listed:
    """One path in one of the two lists, with the rule that put it there."""

    path: str
    reason: str


@dataclass(frozen=True)
class FieldClasses:
    """The two lists of `eval/data-description.md`, each sorted by path."""

    usable: tuple[Listed, ...]
    free_text: tuple[Listed, ...]


# Empty and mixed values


def is_empty(value: object) -> bool:
    """Null, `""`, `[]` or `{}`: ignored when classing, and not filled (AC-15, AC-17)."""
    if value is None:
        return True
    if isinstance(value, str | list | tuple | dict):
        return len(value) == 0
    return False


def kind(value: object) -> str:
    """The type a value counts as when checking for mixed types; a bool is never a number."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dt.datetime):
        return "datetime"
    return type(value).__name__


def is_mixed(summary: PathSummary) -> bool:
    """Non empty values of more than one type; a datetime and a string are both text."""
    families = {"text" if k in ("string", "datetime") else k for k in summary.kinds}
    return len(families) > 1


# The text guard and dates


def passes_text_guard(value: object) -> bool:
    """No character `str.splitlines()` splits on and at most 100 code points; non strings pass."""
    if not isinstance(value, str):
        return True
    return len(value) <= TEXT_LIMIT and value.splitlines() == [value]


def is_date(value: object) -> bool:
    """A datetime, or a string in the AC-17 date form."""
    return isinstance(value, dt.datetime) or (
        isinstance(value, str) and DATE.fullmatch(value) is not None
    )


# What is walked


def walk(path: str, value: object) -> Iterator[tuple[str, object]]:
    """Every non empty leaf under `value`, as `(path, value)`.

    Objects add `.member`; arrays add `[]` and flatten one level per `[]`. An
    object or array is never yielded itself, and empty values are dropped.
    """
    if is_empty(value):
        return
    if isinstance(value, Mapping):
        for member, inner in value.items():
            yield from walk(f"{path}.{member}", inner)
    elif isinstance(value, list | tuple):
        for element in value:
            yield from walk(f"{path}[]", element)
    else:
        yield path, value


def pair_path(name: str, sides: tuple[str, str]) -> str:
    """The one literal path of a changelog item's raw or display pair (AC-16)."""
    return f"changelog.items[field={name}].{sides[0]}/{sides[1]}"


def _member(value: object, *names: str) -> object:
    for name in names:
        if not isinstance(value, Mapping):
            return None
        value = value.get(name)
    return value


def _histories(issue: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    histories = _member(issue, "changelog", "histories")
    if not isinstance(histories, list | tuple):
        return ()
    return tuple(h for h in histories if isinstance(h, Mapping))


def walked_values(issue: Mapping[str, Any]) -> Iterator[tuple[str, object]]:
    """Every non empty value AC-17 walks in one issue, as `(path, value)`.

    The top level `key` and `id`, the `fields` subtree, the three history
    members and each changelog item's two pairs. Nothing else: not `_id`,
    `self`, `expand`, `renderedFields` or any other `changelog` path.
    """
    yield from walk("key", issue.get("key"))
    yield from walk("id", issue.get("id"))
    yield from walk("fields", issue.get("fields"))
    for history in _histories(issue):
        for path, value in zip(
            HISTORY_MEMBERS,
            (_member(history, "author", "key"), history.get("created"), history.get("id")),
            strict=True,
        ):
            if not is_empty(value):
                yield path, value
        for item in history.get("items") or ():
            if not isinstance(item, Mapping):
                continue
            name = item.get("field")
            if not isinstance(name, str) or not name:
                raise EvalError(f"{issue.get('key')}: a changelog item has no field name")
            for sides in (RAW_SIDES, DISPLAY_SIDES):
                for side in sides:
                    if not is_empty(item.get(side)):
                        yield pair_path(name, sides), item[side]


def filled_paths(issue: Mapping[str, Any]) -> frozenset[str]:
    """The walked paths that hold at least one non empty value in this issue (AC-15)."""
    return frozenset(path for path, _ in walked_values(issue))


def summarize(
    observations: Iterable[tuple[str, object]], keep: frozenset[str] = frozenset()
) -> Mapping[str, PathSummary]:
    """Fold `(path, value)` observations into one summary per path.

    Distinct strings are kept up to one past the categorical limit, which is
    enough to class a path; the paths in `keep` hold every distinct string.
    """
    kinds: dict[str, set[str]] = {}
    dates: dict[str, bool] = {}
    guarded: dict[str, bool] = {}
    distinct: dict[str, set[str]] = {}
    for path, value in observations:
        if is_empty(value):
            continue
        kinds.setdefault(path, set()).add(kind(value))
        dates[path] = dates.get(path, True) and is_date(value)
        guarded[path] = guarded.get(path, True) and passes_text_guard(value)
        seen = distinct.setdefault(path, set())
        if isinstance(value, str) and (path in keep or len(seen) <= CATEGORICAL_LIMIT):
            seen.add(value)
    return {
        path: PathSummary(frozenset(kinds[path]), dates[path], guarded[path], frozenset(seen))
        for path, seen in distinct.items()
    }


# Always and never


def is_prose(path: str) -> bool:
    """Always free text, ahead of every other rule (AC-17)."""
    if path in PROSE_FIELDS or path.endswith(".displayName"):
        return True
    if any(path == root or path.startswith((f"{root}.", f"{root}[")) for root in PROSE_SUBTREES):
        return True
    # Changelog items named like a prose field, in any letter case, on both pairs.
    return any(
        path.casefold() == pair_path(name, sides).casefold()
        for name in PROSE_ITEMS
        for sides in (RAW_SIDES, DISPLAY_SIDES)
    )


# Leaves outside the changelog and changelog item pairs


def is_categorical(summary: PathSummary) -> bool:
    """A string path with at most 50 distinct values, every one passing the text guard."""
    return (
        summary.kinds == frozenset({"string"})
        and summary.guarded
        and len(summary.distinct) <= CATEGORICAL_LIMIT
    )


def leaf_class(path: str, summary: PathSummary) -> Listed | None:
    """The structured rule a leaf passes, or None when it is free text.

    Also classes a changelog item's `fromString/toString` pair.
    """
    if summary.kinds == frozenset({"number"}):
        return Listed(path, "number")
    if summary.kinds == frozenset({"boolean"}):
        return Listed(path, "boolean")
    if summary.all_dates:
        return Listed(path, "date")
    if path in ("key", "id") or path.endswith((".key", ".id")):
        return Listed(path, "key or id")
    if path.endswith(".name") and summary.guarded:
        return Listed(path, "name")
    if is_categorical(summary):
        return Listed(path, "categorical")
    return None


def raw_pair_class(path: str, summary: PathSummary) -> Listed | None:
    """A `from/to` pair is structured when it passes the text guard; no distinct value limit."""
    return Listed(path, "raw changelog pair") if summary.guarded else None


def _free_text_reason(summary: PathSummary) -> str:
    if not summary.guarded:
        return "fails the text guard"
    if summary.kinds == frozenset({"string"}):
        return f"more than {CATEGORICAL_LIMIT} distinct values"
    return "not a number, boolean, date or string"


def classify(path: str, summary: PathSummary) -> tuple[bool, Listed]:
    """`(usable, listed)` for one walked path, by the AC-17 rules in their order."""
    if path in ALWAYS_USABLE:
        return True, Listed(path, "always listed")
    if is_prose(path):
        return False, Listed(path, "prose")
    if is_mixed(summary):
        return False, Listed(path, "mixed types")
    is_raw_pair = path.startswith("changelog.items[field=") and path.endswith(".from/to")
    structured = raw_pair_class(path, summary) if is_raw_pair else leaf_class(path, summary)
    if structured is not None:
        return True, structured
    return False, Listed(path, _free_text_reason(summary))


# The two lists


def field_classes(summaries: Mapping[str, PathSummary]) -> FieldClasses:
    """Every walked, non empty path in exactly one list; the always listed paths are added."""
    usable: list[Listed] = [Listed(path, "always listed") for path in ALWAYS_USABLE]
    free_text: list[Listed] = []
    for path in sorted(summaries):
        if _UNWRITABLE.search(path) or path.splitlines() != [path]:
            raise EvalError(f"the path {path!r} cannot be written as one backticked path")
        if path in ALWAYS_USABLE:
            continue
        is_usable, listed = classify(path, summaries[path])
        (usable if is_usable else free_text).append(listed)
    return FieldClasses(
        tuple(sorted(usable, key=lambda item: item.path)),
        tuple(sorted(free_text, key=lambda item: item.path)),
    )


def categorical_paths(summaries: Mapping[str, PathSummary]) -> tuple[str, ...]:
    """The paths whose value lists are written: string paths AC-17 classes categorical.

    Raw `from/to` pairs are left out (the 50 value clause does not apply to
    them), as are prose paths and the always listed members.
    """
    return tuple(
        path
        for path in sorted(summaries)
        if path not in ALWAYS_USABLE
        and not is_prose(path)
        and not path.endswith(".from/to")
        and is_categorical(summaries[path])
    )
