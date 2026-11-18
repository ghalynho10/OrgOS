"""The scan for email addresses and real looking names before publishing (spec 0016, *The scan*).

Pure. The name list is built at check time from exactly four sources in the
corpus fixtures and the data description, trimmed, lower cased, deduplicated,
dropped under 4 characters, sorted and numbered. A match is reported by file,
line and entry number, never by the entry's text. An entry is allowed only by
the SHA 256 in `ALLOWLIST`, mirrored in the spec by hash, source and reason (AC-36).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from orgos.site import SiteError

EMAIL: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")
MIN_LENGTH: Final = 4
PERSON_FIELDS: Final = frozenset(
    {
        "assignee",
        "reporter",
        "dev provided by",
        "full name",
        "e-mail",
        "developed by",
        "qa brought to you by",
        "to be done by",
    }
)
USER_PATHS: Final = frozenset(
    {
        "changelog.histories[].author",
        "fields.comments[].author",
        "fields.comments[].updateAuthor",
        "fields.creator",
        "fields.reporter",
        "fields.assignee",
        "fields.customfield_10560",
        "fields.customfield_10560[]",
    }
)
USER_MARKS: Final = ("displayName", "emailAddress")
USER_VALUES: Final = ("displayName", "name", "key", "emailAddress")
HEADING: Final = re.compile(r"^#+\s*(.*?)\s*#*\s*$")
PERSON_HEADING: Final = re.compile(r"changelog\.items\[field=(.+)\]\.fromString/toString")
VALUE_LINE: Final = re.compile(r'^- ".*"$')

# (the SHA 256 of an allowed entry, the reason), mirrored in spec 0016 *The scan* by the
# hash, its source and the reason; a line is added only with a dated line there.
ALLOWLIST: Final[tuple[tuple[str, str], ...]] = (
    (
        # 2026-11-17, source 1 only.
        "1e32847769dc4a1588004a1dfdf10041407a3494ae910bc76783917fffea7789",
        "the issue tracker's name, in the dataset title (K9) and the conclusion;"
        " from a system mailbox, not a person",
    ),
)


@dataclass(frozen=True)
class NameList:
    """The sorted, numbered name list and the count each source gave."""

    entries: tuple[str, ...]
    source_counts: tuple[int, int, int, int]


@dataclass(frozen=True)
class Match:
    """One match in a published file: where, and which entry by number."""

    file: str
    line: int
    entry: int | None


def _normalise(values: Iterable[str]) -> frozenset[str]:
    cleaned = (value.strip().lower() for value in values)
    return frozenset(value for value in cleaned if len(value) >= MIN_LENGTH)


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _objects(node: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _objects(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value, f"{path}[]")


def _relative(path: str) -> str:
    """A path inside one issue: the fixture files are lists of issues."""
    return path.removeprefix("[]").removeprefix(".")


def email_values(documents: Sequence[Any], description: str) -> frozenset[str]:
    """Source 1: every email in a fixture string or the description, and its part before `@`."""
    found = [
        email
        for text in (*(s for d in documents for s in _strings(d)), description)
        for email in EMAIL.findall(text)
    ]
    return _normalise([*found, *(email.split("@", 1)[0] for email in found)])


def user_values(documents: Sequence[Any]) -> frozenset[str]:
    """Source 2: `displayName`, `name`, `key` and `emailAddress` of every user object."""
    values = []
    for document in documents:
        for path, node in _objects(document):
            if _relative(path) in USER_PATHS or any(mark in node for mark in USER_MARKS):
                values += [node[key] for key in USER_VALUES if isinstance(node.get(key), str)]
    return _normalise(values)


def changelog_values(documents: Sequence[Any]) -> frozenset[str]:
    """Source 3: `fromString` and `toString` of every changelog item on a person field."""
    values = []
    for document in documents:
        for path, node in _objects(document):
            if not _relative(path).endswith("changelog.histories[].items[]"):
                continue
            field = node.get("field")
            if isinstance(field, str) and field.lower() in PERSON_FIELDS:
                values += [
                    node[key]
                    for key in ("fromString", "toString")
                    if isinstance(node.get(key), str)
                ]
    return _normalise(values)


def description_values(description: str) -> frozenset[str]:
    """Source 4: the `- "<value>"` lines under a person field's value list heading.

    The heading, backticks removed, reads `changelog.items[field=<name>].fromString/toString`
    with `<name>` a person field; the list ends at the next heading. Fails naming each
    person field that no heading matches (AC-43), so a format change cannot drop names.
    """
    values = []
    found = set()
    under_person = False
    for line in description.splitlines():
        heading = HEADING.match(line)
        if heading:
            person = PERSON_HEADING.fullmatch(heading.group(1).replace("`", ""))
            name = person.group(1).lower() if person else None
            under_person = name in PERSON_FIELDS
            if under_person:
                found.add(name)
        elif under_person and VALUE_LINE.match(line):
            values.append(line[line.index('"') + 1 : line.rindex('"')])
    unmatched = sorted(PERSON_FIELDS - found)
    if unmatched:
        raise SiteError(
            "name list: no value list heading in the data description for the person field"
            f" {', '.join(unmatched)} (spec 0016 AC-43)"
        )
    return _normalise(values)


def name_list(documents: Sequence[Any], description: str) -> NameList:
    """Build the list from the four sources; a source that yields nothing fails (AC-17)."""
    sources = (
        email_values(documents, description),
        user_values(documents),
        changelog_values(documents),
        description_values(description),
    )
    empty = [str(number) for number, source in enumerate(sources, start=1) if not source]
    if empty:
        raise SiteError(
            f"name list: source {', '.join(empty)} yielded no entry, so a parse rule did not"
            " match (spec 0016, The scan)"
        )
    entries = tuple(sorted(frozenset().union(*sources)))
    counts = (len(sources[0]), len(sources[1]), len(sources[2]), len(sources[3]))
    return NameList(entries, counts)


def entry_hash(entry: str) -> str:
    """The SHA 256 of an entry, the only form the allowlist holds."""
    return hashlib.sha256(entry.encode("utf-8")).hexdigest()


def _line(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def email_matches(files: Mapping[str, str]) -> tuple[Match, ...]:
    """Every email address in the published files, by file and line (AC-15)."""
    return tuple(
        Match(name, _line(content, found.start()), None)
        for name, content in sorted(files.items())
        for found in EMAIL.finditer(content)
    )


def name_matches(
    files: Mapping[str, str],
    names: NameList,
    allowlist: Sequence[tuple[str, str]] = ALLOWLIST,
) -> tuple[Match, ...]:
    """Every list entry found literally, ignoring capitals, with no word character beside it."""
    allowed = {digest for digest, _ in allowlist}
    matches = []
    for number, entry in enumerate(names.entries, start=1):
        if entry_hash(entry) in allowed:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(entry)}(?!\w)", re.IGNORECASE)
        for name, content in sorted(files.items()):
            matches += [
                Match(name, _line(content, found.start()), number)
                for found in pattern.finditer(content)
            ]
    return tuple(sorted(matches, key=lambda match: (match.file, match.line, match.entry or 0)))
