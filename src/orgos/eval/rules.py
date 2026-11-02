"""The one hours, person, boundary, path and time zone rule every known answer uses.

Spec 0005 fixes each rule once (AC-22 to AC-25, AC-48), the same for every
question and the same rule the loads and both stores must follow, so no
question carries its own variant. Each rule is a pure function here, with a
test named for it; the question functions only call them.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from orgos.eval import EvalError
from orgos.eval.entries import TimeEntry, parse_utc


@dataclass(frozen=True)
class IssueLink:
    """One issue link: the issue in its `inwardIssue` end and the one in its `outwardIssue` end."""

    link_id: str
    type_name: str | None
    inward_key: str
    outward_key: str


@dataclass(frozen=True)
class Parent:
    """A parent relation, read from the child's `fields.parent`."""

    child_key: str
    parent_key: str


# Hours and people (AC-22, AC-23)


def counted_entries(
    entries: Iterable[TimeEntry], flagged: Collection[str]
) -> tuple[TimeEntry, ...]:
    """The entries logged hours count: above 0 and not by a flagged account.

    An entry with no author key still counts in hours (AC-20).
    """
    return tuple(
        entry
        for entry in entries
        if entry.amount > 0 and (entry.author_key is None or entry.author_key not in flagged)
    )


def logged_seconds(entries: Iterable[TimeEntry], flagged: Collection[str]) -> int:
    """Logged hours in seconds: the sum of non bot entries above 0 (AC-22)."""
    return sum(entry.amount for entry in counted_entries(entries, flagged))


def people_who_logged(entries: Iterable[TimeEntry], flagged: Collection[str]) -> frozenset[str]:
    """Non bot people with at least one entry above 0 among `entries` (AC-23)."""
    return frozenset(
        entry.author_key
        for entry in counted_entries(entries, flagged)
        if entry.author_key is not None
    )


def role_person(key: object, flagged: Collection[str]) -> str | None:
    """A role field's `.key` as a person, whether or not they logged time; never a bot (AC-23)."""
    if not isinstance(key, str) or not key or key in flagged:
        return None
    return key


# Time in UTC (AC-25)


def utc_time(value: object, issue_key: str) -> dt.datetime:
    """A date value in UTC: a datetime with its zone, or a string with a `+HHMM` offset.

    A missing or unreadable offset stops the run and names the issue.
    """
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise EvalError(f"{issue_key}: a date has no time zone")
        return value.astimezone(dt.UTC)
    return parse_utc(value, issue_key)


def in_interval(moment: dt.datetime, start: dt.datetime, end: dt.datetime) -> bool:
    """`moment` in the half open UTC interval `[start, end)`."""
    return start <= moment < end


# The corpus boundary (AC-24)


def stored_links(issue_key: str, issuelinks: object) -> tuple[IssueLink, ...]:
    """The links stored on one issue, the issue itself filling the end it does not name.

    A link without an id or without its other issue stops the run.
    """
    links: list[IssueLink] = []
    for link in issuelinks if isinstance(issuelinks, list | tuple) else ():
        link_id = link.get("id") if isinstance(link, Mapping) else None
        if link_id is None or link_id == "":
            raise EvalError(f"{issue_key}: an issue link has no id")
        inward, outward = _end(link, "inwardIssue"), _end(link, "outwardIssue")
        if (inward is None) == (outward is None):
            raise EvalError(f"{issue_key}: issue link {link_id} does not name one other issue")
        type_name = (link.get("type") or {}).get("name")
        links.append(
            IssueLink(
                str(link_id),
                type_name if isinstance(type_name, str) else None,
                inward or issue_key,
                outward or issue_key,
            )
        )
    return tuple(links)


def _end(link: Mapping[str, Any], end: str) -> str | None:
    key = (link.get(end) or {}).get("key")
    return key if isinstance(key, str) and key else None


def corpus_links(links: Iterable[IssueLink], corpus_keys: Collection[str]) -> tuple[IssueLink, ...]:
    """Links with both issues in the corpus, each once by its link id, sorted by id."""
    kept = {
        link.link_id: link
        for link in links
        if link.inward_key in corpus_keys and link.outward_key in corpus_keys
    }
    return tuple(kept[link_id] for link_id in sorted(kept))


def corpus_parents(parents: Iterable[Parent], corpus_keys: Collection[str]) -> tuple[Parent, ...]:
    """Parent relations with both issues in the corpus, sorted by child key."""
    return tuple(
        sorted(
            (p for p in parents if p.child_key in corpus_keys and p.parent_key in corpus_keys),
            key=lambda p: p.child_key,
        )
    )


# Paths (AC-48)


def undirected(edges: Iterable[tuple[str, str]]) -> Mapping[str, tuple[str, ...]]:
    """Each entity's neighbours, link direction ignored, sorted."""
    neighbours: dict[str, set[str]] = {}
    for a, b in edges:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    return {entity: tuple(sorted(others)) for entity, others in neighbours.items()}


def simple_paths(
    graph: Mapping[str, Iterable[str]], start: str, end: str, max_hops: int
) -> tuple[tuple[str, ...], ...]:
    """Every path from `start` to `end` that repeats no entity and has at most `max_hops` hops."""

    def walk(path: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
        here = path[-1]
        if here == end and len(path) > 1:
            yield path
            return
        if len(path) - 1 == max_hops:
            return
        for nxt in graph.get(here, ()):
            if nxt not in path:
                yield from walk((*path, nxt))

    return tuple(sorted(walk((start,))))
