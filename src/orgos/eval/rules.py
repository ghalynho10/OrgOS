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
    """One issue link: the issue in its `inwardIssue` end and the one in its `outwardIssue` end.

    Reading along the link's outward label goes from `inward_key` to
    `outward_key`; along its inward label, from `outward_key` to `inward_key`.
    """

    link_id: str
    type_name: str | None
    inward_key: str
    outward_key: str
    outward_label: str | None = None
    inward_label: str | None = None


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


def other_person_share(entries: Iterable[TimeEntry], flagged: Collection[str]) -> tuple[int, int]:
    """Logged seconds credited to someone other than each issue's top person, over all (AC-36).

    The top person has the most positive seconds on the issue, ties to the
    smallest key. Returned as `(num, den)` over every issue with positive
    seconds. AC-36 does not say who an entry with no author is credited to, so
    one stops the run and names it.
    """
    by_issue: dict[str, dict[str, int]] = {}
    for entry in counted_entries(entries, flagged):
        if entry.author_key is None:
            raise EvalError(
                f"{entry.issue_key}: entry {entry.history_id} has no author; "
                "AC-36 does not say who it is credited to"
            )
        people = by_issue.setdefault(entry.issue_key, {})
        people[entry.author_key] = people.get(entry.author_key, 0) + entry.amount
    total = top = 0
    for people in by_issue.values():
        total += sum(people.values())
        top += min(people.items(), key=lambda person: (-person[1], person[0]))[1]
    return total - top, total


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

    A link naming no other issue cannot reach a corpus issue and is left out,
    as `describe` counted it dropped. A link without an id, or naming both
    ends, stops the run.
    """
    links: list[IssueLink] = []
    for link in issuelinks if isinstance(issuelinks, list | tuple) else ():
        link_id = link.get("id") if isinstance(link, Mapping) else None
        if link_id is None or link_id == "":
            raise EvalError(f"{issue_key}: an issue link has no id")
        inward, outward = _end(link, "inwardIssue"), _end(link, "outwardIssue")
        if inward is not None and outward is not None:
            raise EvalError(f"{issue_key}: issue link {link_id} names both of its ends")
        if inward is None and outward is None:
            continue
        kind = link.get("type") or {}
        links.append(
            IssueLink(
                str(link_id),
                _text(kind.get("name")),
                inward or issue_key,
                outward or issue_key,
                _text(kind.get("outward")),
                _text(kind.get("inward")),
            )
        )
    return tuple(links)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _end(link: Mapping[str, Any], end: str) -> str | None:
    return _text((link.get(end) or {}).get("key"))


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


def complete_paths(
    successors: Mapping[str, Iterable[str]],
    predecessors: Mapping[str, Iterable[str]],
    max_hops: int,
) -> tuple[tuple[str, ...], ...]:
    """Every simple path of 1 to `max_hops` hops that cannot be extended at either end.

    An end can be extended when it has a neighbour outside the path, so a
    section of a longer chain is never complete and a chain longer than the
    cap has no complete section. Each path is found once, in the direction of
    `successors`; on an undirected graph that gives both directions of a chain.
    """

    def closed(neighbours: Mapping[str, Iterable[str]], end: str, path: tuple[str, ...]) -> bool:
        return all(other in path for other in neighbours.get(end, ()))

    def walk(path: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
        if (
            len(path) > 1
            and closed(successors, path[-1], path)
            and closed(predecessors, path[0], path)
        ):
            yield path
        if len(path) - 1 < max_hops:
            for nxt in successors.get(path[-1], ()):
                if nxt not in path:
                    yield from walk((*path, nxt))

    return tuple(sorted(path for start in sorted(successors) for path in walk((start,))))
