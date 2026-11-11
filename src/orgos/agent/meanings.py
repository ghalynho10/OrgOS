"""The shared field meanings and the parts both schema descriptions print alike (spec 0012).

`meanings.toml` holds one sentence per field concept with its dump paths,
written once. Both renderers (`describe_sql`, `describe_graph`) take their
words from here and print the general sentences, each meaning and the value
lists through the same functions, so the two descriptions differ only in the
structure they describe (AC-8 to AC-10). Everything here is pure.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orgos.agent.errors import AgentError

MEANINGS_FILE = Path(__file__).with_name("meanings.toml")

# The general sentences, in the order both descriptions print them (AC-10).
GENERAL_KEYS = ("bots", "utc", "missing", "seconds", "link_direction", "folded", "bookkeeping")
# The bookkeeping fields, named once in the `bookkeeping` sentence and nowhere else (AC-9).
SQL_BOOKKEEPING = frozenset({"source_doc_id", "load_id"})
GRAPH_BOOKKEEPING = frozenset({"sourceDocId", "loadId", "edgeId"})
# The meaning keys whose values the descriptions list with their exact spelling (AC-10).
VALUE_KEYS = (
    "issue.status_name",
    "issue.issue_type_name",
    "issue.priority_name",
    "issue.resolution_name",
)
LINK_TYPE_KEY = "issue_link.link_type_name"


@dataclass(frozen=True)
class Meaning:
    """One field concept: its key, its one sentence and the dump paths it was read from."""

    key: str
    meaning: str
    dump: tuple[str, ...]


@dataclass(frozen=True)
class Meanings:
    """The whole meanings file: the general sentences in print order and every field meaning."""

    general: tuple[str, ...]
    fields: Mapping[str, Meaning]


@dataclass(frozen=True)
class LinkWording:
    """One link type as the store holds it: its name and its outward and inward wording."""

    name: str
    outward: str | None
    inward: str | None


@dataclass(frozen=True)
class ValueLists:
    """The values read from a store when the agent is built, listed in its description."""

    values: Mapping[str, tuple[str, ...]]
    link_types: tuple[LinkWording, ...]


@dataclass(frozen=True)
class Description:
    """A rendered schema description and the meaning keys it used."""

    text: str
    keys: frozenset[str]


def parse_meanings(data: Mapping[str, Any]) -> Meanings:
    """The meanings held in the parsed TOML; raises `AgentError` naming every problem."""
    problems: list[str] = []
    general = data.get("general", {})
    problems.extend(
        f"[general] has no sentence {key!r}"
        for key in GENERAL_KEYS
        if not isinstance(general.get(key), str)
    )
    problems.extend(
        f"[general] {key!r} is not a known sentence" for key in general if key not in GENERAL_KEYS
    )
    fields: dict[str, Meaning] = {}
    for index, raw in enumerate(data.get("field", ())):
        key, meaning, dump = raw.get("key"), raw.get("meaning"), raw.get("dump", [])
        if not isinstance(key, str) or not isinstance(meaning, str) or not meaning.strip():
            problems.append(f"field {index + 1} needs a key and a meaning")
            continue
        if not isinstance(dump, list) or not all(isinstance(path, str) for path in dump):
            problems.append(f"{key}: dump must be a list of paths")
            continue
        if key in fields:
            problems.append(f"{key} is listed twice")
        fields[key] = Meaning(key=key, meaning=meaning, dump=tuple(dump))
    if problems:
        raise AgentError(f"{MEANINGS_FILE.name} is not usable:\n  " + "\n  ".join(problems))
    return Meanings(general=tuple(general[key] for key in GENERAL_KEYS), fields=fields)


def load_meanings(path: Path = MEANINGS_FILE) -> Meanings:
    """Read and check the meanings file."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise AgentError(f"Cannot read {path}: {error.strerror}") from None
    except tomllib.TOMLDecodeError as error:
        raise AgentError(f"{path} is not valid TOML: {error}") from None
    return parse_meanings(data)


def meaning_text(meanings: Meanings, key: str) -> str:
    """The sentence of `key` with its dump paths, printed the same way in both descriptions."""
    meaning = meanings.fields.get(key)
    if meaning is None:
        raise AgentError(f"{MEANINGS_FILE.name} has no meaning for {key!r}")
    if not meaning.dump:
        return meaning.meaning
    return f"{meaning.meaning} (dump: {', '.join(f'`{path}`' for path in meaning.dump)})"


def general_section(meanings: Meanings) -> list[str]:
    """The sentences both descriptions open with."""
    return ["About the data", *(f"- {sentence}" for sentence in meanings.general)]


def _quoted(value: str | None) -> str:
    return "(missing)" if value is None else f'"{value}"'


def values_section(
    values: ValueLists,
    field_names: Mapping[str, str],
    link_type_name: Callable[[str], str],
) -> list[str]:
    """The exact spelling of every listed value, sorted, and the wording of each link type.

    `field_names` gives the store's name for each of `VALUE_KEYS` and for the
    link type; `link_type_name` the store's name for one link type.
    """
    lines = ["Values, exact spelling"]
    for key in VALUE_KEYS:
        listed = ", ".join(_quoted(value) for value in sorted(values.values.get(key, ())))
        lines.append(f"- {field_names[key]}: {listed}")
    lines.append(f"Link types ({field_names[LINK_TYPE_KEY]}): outward wording / inward wording")
    ordered = sorted(
        values.link_types, key=lambda link: (link.name, link.outward or "", link.inward or "")
    )
    lines.extend(
        f"- {link_type_name(link.name)}: {_quoted(link.outward)} / {_quoted(link.inward)}"
        for link in ordered
    )
    return lines
