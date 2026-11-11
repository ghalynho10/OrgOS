"""The literal check: no gold query may carry a piece of its known answer (spec 0011 AC-14).

A query file is refused when it holds a whole token equal to a known answer key
or source ID that the question's `text` and `reading` do not name, or a numeric
literal of 5 or more digits equal to a number in the known answer. A token is
a maximal run of letters, digits, hyphens and underscores, and the contents of
each quoted string are checked as one token too. The message gives the file
and line only, never the literal. Smaller numbers are not caught (a count of 3
matches `LIMIT 3`), so `gold/CHECKLIST.md` asks you to read for them. Pure.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

TOKEN = re.compile(r"[A-Za-z0-9_-]+")
QUOTED = re.compile(r"'([^']*)'|\"([^\"]*)\"")
DIGITS = re.compile(r"\d+")
MIN_DIGITS = 5
HIDDEN = "<hidden>"


def tokens(text: str) -> Iterator[str]:
    """Every whole token of a text, then the contents of each quoted string."""
    yield from TOKEN.findall(text)
    for match in QUOTED.finditer(text):
        yield match.group(1) if match.group(1) is not None else match.group(2)


def _ids(item: str) -> tuple[str, ...]:
    # A typed answer key `kind:id` counts both whole and as its bare id.
    kind, sep, bare = item.partition(":")
    return (item, bare) if sep and kind and bare else (item,)


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _walk(item)


def _step_ids(steps: Iterable[Mapping[str, Iterable[object]]]) -> Iterator[str]:
    for step in steps:
        for ids in step.values():
            yield from (str(item) for item in ids)


def known_tokens(known: Mapping[str, Any]) -> frozenset[str]:
    """Every answer key and source ID of a known answer, typed keys also as bare IDs."""
    shape, value = known["shape"], known["value"]
    items = list(_step_ids(known["sources"]))
    if shape == "map":
        items.extend(str(item["id"]) for item in value)
    elif shape == "ranking":
        items.extend(str(key) for group in value for key in group["ids"])
    elif shape == "set":
        items.extend(str(key) for key in value)
    elif shape == "path":
        for item in value:
            items.extend(str(key) for key in item["path"])
            items.extend(_step_ids(item["sources"]))
    return frozenset(token for item in items for token in _ids(item))


def known_numbers(known: Mapping[str, Any]) -> frozenset[int]:
    """Every integer in the known answer's value with 5 or more digits."""
    numbers = {
        number
        for node in _walk(known["value"])
        if isinstance(node, Mapping)
        for key in ("int", "num", "den")
        if type(number := node.get(key)) is int
    }
    return frozenset(n for n in numbers if len(str(abs(n))) >= MIN_DIGITS)


def forbidden(known: Mapping[str, Any], allowed_text: str) -> frozenset[str]:
    """The known tokens a query may not hold: those the question text and reading do not name."""
    return known_tokens(known) - set(tokens(allowed_text))


def _line_hits(line: str, banned: frozenset[str], numbers: frozenset[int]) -> bool:
    if any(token in banned for token in tokens(line)):
        return True
    return any(len(run) >= MIN_DIGITS and int(run) in numbers for run in DIGITS.findall(line))


def literal_problems(
    files: Iterable[tuple[str, str]], known: Mapping[str, Any], allowed_text: str
) -> tuple[str, ...]:
    """One problem per offending line, as `<file>:<line>`, for `(name, text)` query files."""
    banned = forbidden(known, allowed_text)
    numbers = known_numbers(known)
    return tuple(
        f"{name}:{number}: holds a literal from the known answer"
        for name, text in files
        for number, line in enumerate(text.splitlines(), start=1)
        if _line_hits(line, banned, numbers)
    )


def mask(text: str, known: Mapping[str, Any]) -> str:
    """A store message with every known key, ID and long known number replaced by `<hidden>`.

    Used on database error lines, so a refusal that echoes a value never prints it.
    """
    banned = known_tokens(known)
    numbers = known_numbers(known)

    def hide(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in banned or (token.isdigit() and int(token) in numbers):
            return HIDDEN
        return token

    return TOKEN.sub(hide, text)
