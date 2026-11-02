"""The corpus snapshot the known answers are computed from (spec 0005, Feature design).

The shell reads raw Mongo through the reader login with `find`, a filter and a
projection only (AC-18), and hands the decoded documents here. Every count is
made in Python over this frozen snapshot: the corpus issues with only the
fields the questions cite, their time entries, and the issue links and parent
relations the boundary rule keeps (AC-24). Copies of one issue collapse to one
document first (AC-26). Pure: documents in, snapshot out.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from bson import json_util
from bson.json_util import CANONICAL_JSON_OPTIONS

from orgos.eval import EvalError
from orgos.eval.describe import project_of
from orgos.eval.entries import ENTRY_FIELD, TimeEntry, rebuild_entries
from orgos.eval.fields import walked_values
from orgos.eval.rules import IssueLink, Parent, corpus_links, corpus_parents, stored_links

# What every history read needs: its id, time, author and each item's pairs.
HISTORY_PROJECTION = {
    "changelog.histories.id": 1,
    "changelog.histories.created": 1,
    "changelog.histories.author.key": 1,
    "changelog.histories.author.accountId": 1,
    "changelog.histories.author.displayName": 1,
    "changelog.histories.items.field": 1,
    "changelog.histories.items.fieldtype": 1,
    "changelog.histories.items.from": 1,
    "changelog.histories.items.to": 1,
    "changelog.histories.items.fromString": 1,
    "changelog.histories.items.toString": 1,
}
# What every corpus issue needs, whatever the questions cite.
ISSUE_PROJECTION = {
    "key": 1,
    "fields.project.key": 1,
    "fields.project.id": 1,
    "fields.timespent": 1,
    "fields.parent.key": 1,
    "fields.issuelinks.id": 1,
    "fields.issuelinks.type.name": 1,
    "fields.issuelinks.inwardIssue.key": 1,
    "fields.issuelinks.outwardIssue.key": 1,
}
# What finding the keys held by more than one corpus document needs; `_id` comes by default.
KEY_PROJECTION = {"key": 1}
# Every issue of the collection that holds a `timespent` item, for the bot flags (AC-21).
ENTRY_ISSUES_FILTER = {"changelog.histories.items.field": ENTRY_FIELD}
ENTRY_ISSUES_PROJECTION = {
    "_id": 0,
    "key": 1,
    "fields.project.key": 1,
    **HISTORY_PROJECTION,
}


@dataclass(frozen=True)
class Issue:
    """One corpus issue, holding only the cited paths among its fields."""

    key: str
    project_key: str
    project_id: str | None
    timespent: int | None
    parent_key: str | None
    links: tuple[IssueLink, ...]
    history_ids: tuple[str, ...]
    # (person key, display name) of each history author that has the person field.
    authors: tuple[tuple[str, str | None], ...]
    # Each cited path with its non empty values, in document order (AC-16 paths).
    cited: tuple[tuple[str, tuple[object, ...]], ...]

    def values(self, path: str) -> tuple[object, ...]:
        """The non empty values at a cited path; empty when missing, null or `""` (AC-15)."""
        for cited_path, values in self.cited:
            if cited_path == path:
                return values
        raise EvalError(f"{path} is not a cited path of the snapshot")


@dataclass(frozen=True)
class Collapsed:
    """One issue key held by several identical documents: the `_id` kept and those dropped."""

    key: str
    kept: str
    dropped: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        """The run record object for this key."""
        return {"kept": self.kept, "dropped": list(self.dropped)}


@dataclass(frozen=True)
class Snapshot:
    """The corpus as the answers see it (the spec's in memory `Corpus`)."""

    projects: tuple[str, ...]
    person_field: str
    flagged: frozenset[str]
    issues: tuple[Issue, ...]
    entries: tuple[TimeEntry, ...]
    links: tuple[IssueLink, ...]
    parents: tuple[Parent, ...]
    collapsed: tuple[Collapsed, ...] = ()


def corpus_filter(projects: Iterable[str]) -> dict[str, Any]:
    """Corpus issues by current `fields.project.key` (AC-24)."""
    return {"fields.project.key": {"$in": list(projects)}}


def repeated_keys(documents: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """The issue keys held by more than one document, sorted."""
    held = Counter(key for key in (document.get("key") for document in documents) if key)
    return tuple(sorted(key for key, n in held.items() if n > 1))


def _without_id(document: Mapping[str, Any]) -> str:
    # Canonical JSON keeps BSON types apart, so 1 and 1.0 differ as they do in the dump.
    rest = {name: value for name, value in document.items() if name != "_id"}
    return json_util.dumps(rest, json_options=CANONICAL_JSON_OPTIONS, sort_keys=True)


def collapse(documents: Iterable[Mapping[str, Any]]) -> tuple[Collapsed, ...]:
    """The full documents of repeated keys, each key down to its lowest `_id` (AC-26).

    Documents of one key that differ in any field besides `_id` stop the run
    and name the key. A key on one document has nothing to collapse.
    """
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for document in documents:
        by_key.setdefault(document["key"], []).append(document)
    collapsed: list[Collapsed] = []
    for key in sorted(by_key):
        copies = sorted(by_key[key], key=lambda document: document["_id"])
        if len(copies) == 1:
            continue
        if len({_without_id(copy) for copy in copies}) > 1:
            raise EvalError(
                f"uniqueness: {key} is on {len(copies)} documents that differ besides _id"
            )
        kept, *dropped = (str(copy["_id"]) for copy in copies)
        collapsed.append(Collapsed(key, kept, tuple(dropped)))
    return tuple(collapsed)


def corpus_projection(paths: Iterable[str]) -> dict[str, int]:
    """The issue projection plus each cited `fields` path, `[]` dropped as Mongo reads arrays.

    Changelog paths are read through the history projection.
    """
    projection: dict[str, int] = {**ISSUE_PROJECTION, **HISTORY_PROJECTION}
    for path in paths:
        if path.startswith("fields."):
            projection[path.replace("[]", "")] = 1
        elif path in ("key", "id"):
            projection[path] = 1
    return projection


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _timespent(fields: Mapping[str, Any], key: str) -> int | None:
    value = fields.get("timespent")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvalError(f"{key}: fields.timespent is not an integer")
    return value


def issue_from(document: Mapping[str, Any], paths: Collection[str], person_field: str) -> Issue:
    """One corpus issue from its projected document; only `paths` among its walked values."""
    key = _text(document.get("key"))
    if key is None:
        raise EvalError("an issue has no key")
    fields = document.get("fields") or {}
    histories = (document.get("changelog") or {}).get("histories") or ()
    cited: dict[str, list[object]] = {path: [] for path in sorted(paths)}
    for path, value in walked_values(document):
        if path in cited:
            cited[path].append(value)
    project_id = (fields.get("project") or {}).get("id")
    authors = []
    for history in histories:
        author = history.get("author") or {}
        person = _text(author.get(person_field))
        if person is not None:
            authors.append((person, _text(author.get("displayName"))))
    return Issue(
        key=key,
        project_key=project_of(document),
        project_id=_text(str(project_id) if project_id is not None else None),
        timespent=_timespent(fields, key),
        parent_key=_text((fields.get("parent") or {}).get("key")),
        links=stored_links(key, fields.get("issuelinks")),
        history_ids=tuple(str(h["id"]) for h in histories if h.get("id") not in (None, "")),
        authors=tuple(authors),
        cited=tuple((path, tuple(values)) for path, values in cited.items()),
    )


def build_snapshot(
    projects: tuple[str, ...],
    person_field: str,
    flagged: frozenset[str],
    documents: Iterable[Mapping[str, Any]],
    paths: Collection[str],
    collapsed: tuple[Collapsed, ...] = (),
) -> Snapshot:
    """The snapshot of the corpus documents: issues, every entry, and the kept links and parents.

    A dropped copy is skipped before anything is read from it (AC-26).
    """
    dropped = frozenset(copy for each in collapsed for copy in each.dropped)
    issues: list[Issue] = []
    entries: list[TimeEntry] = []
    for document in documents:
        if str(document.get("_id")) in dropped:
            continue
        issues.append(issue_from(document, paths, person_field))
        entries.extend(rebuild_entries(document, person_field))
    keys = frozenset(issue.key for issue in issues)
    return Snapshot(
        projects=projects,
        person_field=person_field,
        flagged=flagged,
        issues=tuple(issues),
        entries=tuple(entries),
        links=corpus_links((link for issue in issues for link in issue.links), keys),
        parents=corpus_parents(
            (Parent(i.key, i.parent_key) for i in issues if i.parent_key is not None), keys
        ),
        collapsed=collapsed,
    )
