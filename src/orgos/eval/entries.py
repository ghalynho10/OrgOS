"""Time entries rebuilt from an issue's changelog, written fresh from spec 0004 AC-7.

There is no worklog field in the dump, so logged time is read from the
changelog: each history holding a `timespent` item of fieldtype `jira` is one
entry. These functions are the known answer code's own reading of that rule
(spec 0005, AC-20), kept apart from the spike and from the loads, so a shared
bug cannot pass unseen. Pure: a document goes in, frozen entries come out.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from orgos.eval import EvalError

ENTRY_FIELD = "timespent"
ENTRY_FIELDTYPE = "jira"

INTEGER = re.compile(r"-?[0-9]+")
# An ISO 8601 time that ends in a `+HHMM` or `-HHMM` offset, as Jira writes it.
OFFSET_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?[+-][0-9]{4}"
)


@dataclass(frozen=True)
class TimeEntry:
    """One rebuilt time entry; `author_key` is None when the author lacks the person field."""

    history_id: str
    issue_key: str
    project_key: str | None
    author_key: str | None
    created_utc: dt.datetime
    amount: int


def parse_utc(value: object, issue_key: str) -> dt.datetime:
    """A history's `created` as UTC; a missing or unreadable offset stops the run (AC-25)."""
    if not isinstance(value, str) or not OFFSET_TIME.fullmatch(value):
        raise EvalError(f"{issue_key}: a changelog time has no +HHMM offset or cannot be read")
    return dt.datetime.fromisoformat(value).astimezone(dt.UTC)


def _side(raw: object, string: object, issue_key: str) -> int:
    # The raw value wins; the display string is read only when the raw value is
    # null or empty, and both empty count as 0.
    value = string if raw is None or raw == "" else raw
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise EvalError(f"{issue_key}: a time entry value is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and INTEGER.fullmatch(value):
        return int(value)
    raise EvalError(f"{issue_key}: a time entry value is not an integer")


def _amount(item: Mapping[str, Any], issue_key: str) -> int:
    return _side(item.get("to"), item.get("toString"), issue_key) - _side(
        item.get("from"), item.get("fromString"), issue_key
    )


def _is_entry_item(item: Mapping[str, Any]) -> bool:
    return item.get("field") == ENTRY_FIELD and item.get("fieldtype") == ENTRY_FIELDTYPE


def _person(author: object, person_field: str) -> str | None:
    if not isinstance(author, Mapping):
        return None
    value = author.get(person_field)
    return value if isinstance(value, str) and value else None


def _entries(issue: Mapping[str, Any], person_field: str) -> Iterator[TimeEntry]:
    issue_key = issue.get("key")
    if not isinstance(issue_key, str) or not issue_key:
        raise EvalError("an issue has no key")
    project = (issue.get("fields") or {}).get("project") or {}
    project_key = project.get("key") if isinstance(project.get("key"), str) else None
    for history in (issue.get("changelog") or {}).get("histories") or ():
        items = [item for item in history.get("items") or () if _is_entry_item(item)]
        if not items:
            continue
        history_id = history.get("id")
        if history_id is None or history_id == "":
            raise EvalError(f"{issue_key}: a time entry's changelog history has no id")
        yield TimeEntry(
            history_id=str(history_id),
            issue_key=issue_key,
            project_key=project_key,
            author_key=_person(history.get("author"), person_field),
            created_utc=parse_utc(history.get("created"), issue_key),
            # Two `timespent` items in one history are one entry.
            amount=sum(_amount(item, issue_key) for item in items),
        )


def rebuild_entries(issue: Mapping[str, Any], person_field: str) -> tuple[TimeEntry, ...]:
    """Every time entry of one issue document, in changelog order (spec 0004 AC-7).

    An entry is a changelog history holding an item with `field` `timespent`
    and `fieldtype` `jira`. Its amount is `to` minus `from` in seconds; a
    negative amount is a decrease or removal. Its ID, author and time come from
    the history. A history with no id, a value that is not an integer, or a
    time without its offset stops the run, naming the issue.
    """
    return tuple(_entries(issue, person_field))
