"""The known answer functions `q01` to `q20` and the registry (spec 0005, AC-28, AC-32).

Each function is written from your reading note for its question and is
registered under its id with the constants that note fixes: the scope, the
date window, the cut, the ranking direction, `max_hops`, and the kinds each
source step lists. A function reads only the snapshot and its own registered
constants, and calls the shared rules in `orgos.eval.rules` for hours, people,
the boundary, paths and UTC, so no question carries its own variant.
"""

from __future__ import annotations

import datetime as dt
import re
import types
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise

from orgos.eval import EvalError
from orgos.eval.entries import TimeEntry
from orgos.eval.models import Answer, Number, SourceKind, TieGroup
from orgos.eval.rules import (
    IssueLink,
    complete_paths,
    counted_entries,
    in_interval,
    role_person,
    utc_time,
)
from orgos.eval.snapshot import Issue, Snapshot
from orgos.eval.writer import (
    Direction,
    boolean_answer,
    count,
    map_answer,
    number_answer,
    path_answer,
    ranked,
    set_answer,
    step,
    tie_groups,
    typed,
    typed_set_answer,
)

Window = tuple[dt.datetime, dt.datetime]

DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Registered:
    """One question's function and the constants its reading note fixes (AC-28)."""

    function: Callable[[Snapshot, Registered], Answer]
    # The projects the reading's population names, by current `fields.project.key`.
    scope: tuple[str, ...]
    # The kinds each source step lists, one entry per step; for a path, the kinds of
    # the step every hop has (AC-32).
    steps: tuple[tuple[SourceKind, ...], ...]
    # A half open UTC date window `[start, end)` (AC-25), when the reading sets one.
    window: Window | None = None
    cut: int | None = None
    direction: Direction | None = None
    max_hops: int | None = None
    # The one issue the question starts from, when its reading names one.
    anchor: str | None = None


def in_scope(snapshot: Snapshot, spec: Registered) -> tuple[Issue, ...]:
    """The corpus issues whose current project is in the registered scope."""
    outside = sorted(set(spec.scope) - set(snapshot.projects))
    if outside:
        raise EvalError(f"the registered scope names projects outside the corpus: {outside}")
    projects = frozenset(spec.scope)
    return tuple(issue for issue in snapshot.issues if issue.project_key in projects)


def single(issue: Issue, path: str) -> object:
    """The one value at `path`; none or several stops the run and names the issue."""
    values = issue.values(path)
    if len(values) != 1:
        raise EvalError(f"{issue.key}: {path} holds {len(values)} values, not one")
    return values[0]


def optional(issue: Issue, path: str) -> object | None:
    """The value at `path`, or None when missing, null or `""`; several stops the run."""
    values = issue.values(path)
    if len(values) > 1:
        raise EvalError(f"{issue.key}: {path} holds {len(values)} values, not one")
    return values[0] if values else None


def window(spec: Registered) -> Window:
    """The registered date window; a function that needs one stops without it."""
    if spec.window is None:
        raise EvalError("this question's registration has no date window")
    return spec.window


def scope_entries(snapshot: Snapshot, spec: Registered) -> tuple[TimeEntry, ...]:
    """The entries logged hours count (AC-22) on issues in the registered scope."""
    projects = frozenset(spec.scope)
    return tuple(
        entry
        for entry in counted_entries(snapshot.entries, snapshot.flagged)
        if entry.project_key in projects
    )


def estimate(issue: Issue) -> int | None:
    """The current `fields.timeoriginalestimate` in seconds; anything but an integer stops."""
    value = optional(issue, "fields.timeoriginalestimate")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvalError(f"{issue.key}: fields.timeoriginalestimate is not an integer")
    return value


def seconds_per(entries: Iterable[TimeEntry], key: Callable[[TimeEntry], str]) -> dict[str, int]:
    """The entries' amounts summed under each `key`."""
    totals: dict[str, int] = {}
    for entry in entries:
        totals[key(entry)] = totals.get(key(entry), 0) + entry.amount
    return totals


def issue_count(keys: Iterable[str]) -> Answer:
    """A count of issue records; a count of 0 has the empty step list `[]`."""
    issues = tuple(keys)
    return number_answer(count(len(issues)), (step({"issue": issues}),) if issues else ())


def top_groups(
    kind: str, totals: Mapping[str, int], spec: Registered
) -> tuple[tuple[TieGroup, ...], frozenset[str]]:
    """The tie groups at the registered cut and direction, and the bare ids they return."""
    if spec.cut is None or spec.direction is None:
        raise EvalError("this question's registration has no cut or direction")
    values = {key: count(total) for key, total in totals.items()}
    groups = tie_groups(kind, values, spec.direction, spec.cut)
    return groups, frozenset(i.removeprefix(f"{kind}:") for group in groups for i in group.ids)


# Q01: aggregate, map, count


def q01(snapshot: Snapshot, spec: Registered) -> Answer:
    """Per project, the issues created in the window that are still unresolved and unassigned.

    The reading: the population is the seven projects by current
    `fields.project.key`; `fields.created` in `[2013-01-01, 2013-04-01)` UTC;
    unresolved and unassigned mean `fields.resolution.name` and
    `fields.assignee.key` are missing, null or `""` (a missing containing
    object included). Every project is listed, zeros included. Sources: one
    step listing the qualifying issue records; no changelog is used.
    """
    start, end = window(spec)
    qualifying: dict[str, list[str]] = {project: [] for project in spec.scope}
    for issue in in_scope(snapshot, spec):
        created = utc_time(single(issue, "fields.created"), issue.key)
        if (
            in_interval(created, start, end)
            and not issue.values("fields.resolution.name")
            and not issue.values("fields.assignee.key")
        ):
            qualifying[issue.project_key].append(issue.key)
    counts: Mapping[str, Number] = {p: count(len(keys)) for p, keys in qualifying.items()}
    issues = [key for keys in qualifying.values() for key in keys]
    return map_answer("project", counts, (step({"issue": issues}),))


# Q02: aggregate, number, count


def q02(snapshot: Snapshot, spec: Registered) -> Answer:
    """The issues in the scope marked Fixed in at least one version not yet released.

    The reading: `fields.resolution.name` equals "Fixed", and at least one
    `fields.fixVersions[].released` value equals false; each issue counts once.
    Sources: one step listing the qualifying issue records.
    """
    return issue_count(
        issue.key
        for issue in in_scope(snapshot, spec)
        if optional(issue, "fields.resolution.name") == "Fixed"
        and any(value is False for value in issue.values("fields.fixVersions[].released"))
    )


# Q03: aggregate, number, count


def due_instant(value: object, issue_key: str) -> dt.datetime:
    """A due value in UTC; a date only value is midnight UTC at the start of that day."""
    if isinstance(value, str) and DATE_ONLY.fullmatch(value):
        try:
            return dt.datetime.fromisoformat(value).replace(tzinfo=dt.UTC)
        except ValueError:
            raise EvalError(f"{issue_key}: fields.duedate cannot be read") from None
    return utc_time(value, issue_key)


def _resolved_late(issue: Issue, start: dt.datetime, end: dt.datetime) -> bool:
    if optional(issue, "fields.resolution.name") is None:
        return False
    resolved = optional(issue, "fields.resolutiondate")
    if resolved is None:
        return False
    at = utc_time(resolved, issue.key)
    if not in_interval(at, start, end):
        return False
    due = optional(issue, "fields.duedate")
    return due is not None and at > due_instant(due, issue.key)


def q03(snapshot: Snapshot, spec: Registered) -> Answer:
    """The issues in the scope resolved in the window, strictly after their due date.

    The reading: `fields.resolution.name` exists and is not `""`, no status
    condition; `fields.resolutiondate` in `[2012-04-01, 2012-07-01)` UTC and
    strictly later than `fields.duedate`, which must be present. Sources: one
    step listing the qualifying issue records.
    """
    start, end = window(spec)
    return issue_count(
        issue.key for issue in in_scope(snapshot, spec) if _resolved_late(issue, start, end)
    )


# Q04: aggregate, map, seconds


def q04(snapshot: Snapshot, spec: Registered) -> Answer:
    """Per project, total logged seconds minus total original estimated seconds.

    The reading: logged time is the positive, non bot entries; a missing
    estimate contributes 0; every project is listed. Sources: one step listing
    the issues with at least one such entry or a non null estimate (an estimate
    of 0 counts as one, your ruling of 2026-11-02) and those entries.
    """
    entries = scope_entries(snapshot, spec)
    totals = {project: 0 for project in spec.scope}
    with_entry = frozenset(entry.issue_key for entry in entries)
    listed: list[str] = []
    for project, logged in seconds_per(entries, lambda e: str(e.project_key)).items():
        totals[project] += logged
    for issue in in_scope(snapshot, spec):
        estimated = estimate(issue)
        if estimated is not None:
            totals[issue.project_key] -= estimated
        if estimated is not None or issue.key in with_entry:
            listed.append(issue.key)
    values = {project: count(total) for project, total in totals.items()}
    sources = step({"issue": listed, "time_entry": [e.history_id for e in entries]})
    return map_answer("project", values, (sources,))


# Q05: aggregate, map, seconds


def q05(snapshot: Snapshot, spec: Registered) -> Answer:
    """Per person, the seconds they logged on issues currently assigned to someone else.

    The reading: an issue with no assignee or a flagged assignee is left out;
    a person is a non bot entry author, so an entry with no author is not
    summed for anyone; the author's key differs from the current assignee's.
    Sources: one step listing the contributing issues and time entries.
    """
    assignees = {
        issue.key: role_person(optional(issue, "fields.assignee.key"), snapshot.flagged)
        for issue in in_scope(snapshot, spec)
    }
    kept = tuple(
        entry
        for entry in scope_entries(snapshot, spec)
        if entry.author_key is not None
        and assignees[entry.issue_key] is not None
        and entry.author_key != assignees[entry.issue_key]
    )
    totals = seconds_per(kept, lambda e: str(e.author_key))
    values: Mapping[str, Number] = {person: count(total) for person, total in totals.items()}
    sources = step(
        {
            "issue": {entry.issue_key for entry in kept},
            "time_entry": [entry.history_id for entry in kept],
        }
    )
    return map_answer("person", values, (sources,) if kept else ())


# Q06: aggregate, ranking, seconds


def q06(snapshot: Snapshot, spec: Registered) -> Answer:
    """The top issues by seconds logged in the window, among those currently unresolved.

    The reading: current `fields.resolution.name` is missing, null or `""`; an
    entry counts when its `created` is in `[2009-10-01, 2010-01-01)` UTC;
    highest first, cut at five, ties at the fifth kept. Sources: one step
    listing the returned issues and their counted entries; the project key is
    a display value (your ruling of 2026-11-02).
    """
    start, end = window(spec)
    unresolved = frozenset(
        issue.key
        for issue in in_scope(snapshot, spec)
        if optional(issue, "fields.resolution.name") is None
    )
    kept = tuple(
        entry
        for entry in scope_entries(snapshot, spec)
        if entry.issue_key in unresolved and in_interval(entry.created_utc, start, end)
    )
    groups, returned = top_groups("issue", seconds_per(kept, lambda e: e.issue_key), spec)
    contributing = [entry.history_id for entry in kept if entry.issue_key in returned]
    sources = step({"issue": returned, "time_entry": contributing})
    return ranked(groups, (sources,) if returned else ())


# Q07: aggregate, ranking, seconds


def q07(snapshot: Snapshot, spec: Registered) -> Answer:
    """The top issues by the absolute difference between estimate and logged seconds.

    The reading: the current estimate is above 0 and the issue has at least one
    positive, non bot entry; highest first, cut at ten, ties at the tenth kept.
    Sources: one step listing the returned issues, their counted entries, and
    one flat list of the distinct non bot authors across them (your ruling of
    2026-11-02; an entry with no author counts in hours and is no person).
    """
    entries = scope_entries(snapshot, spec)
    logged = seconds_per(entries, lambda e: e.issue_key)
    differences: dict[str, int] = {}
    for issue in in_scope(snapshot, spec):
        estimated = estimate(issue)
        if estimated is not None and estimated > 0 and issue.key in logged:
            differences[issue.key] = abs(estimated - logged[issue.key])
    groups, returned = top_groups("issue", differences, spec)
    contributing = tuple(entry for entry in entries if entry.issue_key in returned)
    sources = step(
        {
            "issue": returned,
            "time_entry": [entry.history_id for entry in contributing],
            "person": {e.author_key for e in contributing if e.author_key is not None},
        }
    )
    return ranked(groups, (sources,) if returned else ())


# Paths over issue links (AC-24, AC-48)

Hop = tuple[str, str]


def chain_answer(between: Mapping[Hop, Iterable[str]], spec: Registered) -> Answer:
    """The complete chains over directed hops, each hop's step its two issues and its links.

    Several links between the same two issues stay one path, with all their
    ids in that hop's step (your ruling of 2026-11-02).
    """
    if spec.max_hops is None:
        raise EvalError("this question's registration has no max_hops")
    successors: dict[str, set[str]] = {}
    predecessors: dict[str, set[str]] = {}
    for start, end in between:
        successors.setdefault(start, set()).add(end)
        predecessors.setdefault(end, set()).add(start)
    paths = complete_paths(successors, predecessors, spec.max_hops)
    return path_answer(
        (
            [typed("issue", key) for key in path],
            [step({"issue": hop, "issue_link": between[hop]}) for hop in pairwise(path)],
        )
        for path in paths
    )


def link_hops(hops: Iterable[tuple[Hop, IssueLink]]) -> dict[Hop, set[str]]:
    """Each directed hop with the ids of every link that supports it."""
    between: dict[Hop, set[str]] = {}
    for hop, link in hops:
        between.setdefault(hop, set()).add(link.link_id)
    return between


# Q08: path_connection, path


def q08(snapshot: Snapshot, spec: Registered) -> Answer:
    """The complete chains of outward "depends on" links among unresolved issues.

    The reading: every issue is in the scope with `fields.resolution.name`
    missing, null or `""`; links are followed only from the source to its
    `outwardIssue`; a chain cannot be extended at either end and has at most
    `max_hops` hops. A one hop chain counts (your ruling of 2026-11-02). Each
    hop's step lists its link ids and both of its issues.
    """
    unresolved = frozenset(
        issue.key
        for issue in in_scope(snapshot, spec)
        if optional(issue, "fields.resolution.name") is None
    )
    return chain_answer(
        link_hops(
            ((link.inward_key, link.outward_key), link)
            for link in snapshot.links
            if link.outward_label == "depends on"
            and link.inward_key in unresolved
            and link.outward_key in unresolved
        ),
        spec,
    )


# Q09: path_connection, path


def assignees_in_scope(snapshot: Snapshot, spec: Registered) -> dict[str, str]:
    """Each issue in the scope with its qualifying current assignee: non bot and filled."""
    found = {
        issue.key: role_person(optional(issue, "fields.assignee.key"), snapshot.flagged)
        for issue in in_scope(snapshot, spec)
    }
    return {key: person for key, person in found.items() if person is not None}


def q09(snapshot: Snapshot, spec: Registered) -> Answer:
    """The complete chains of any link type where consecutive issues have different assignees.

    The reading: every issue is in the scope with a qualifying current
    assignee; links are followed in either direction; a chain cannot be
    extended at either end and has at most `max_hops` hops. A one hop chain
    counts, and each chain is stored in both directions as two paths (your
    rulings of 2026-11-02). Each hop's step lists its link ids and both issues.
    """
    assignees = assignees_in_scope(snapshot, spec)
    kept = tuple(
        link
        for link in snapshot.links
        if link.inward_key in assignees
        and link.outward_key in assignees
        and assignees[link.inward_key] != assignees[link.outward_key]
    )
    return chain_answer(
        link_hops(
            (hop, link)
            for link in kept
            for hop in (
                (link.inward_key, link.outward_key),
                (link.outward_key, link.inward_key),
            )
        ),
        spec,
    )


# Q10: path_connection, set


OUTWARD_RELATIONS = frozenset({"blocks", "depends on", "duplicates"})
INWARD_RELATIONS = frozenset({"supersedes"})


def _relations(link: IssueLink) -> Iterable[Hop]:
    if link.outward_label in OUTWARD_RELATIONS:
        yield link.inward_key, link.outward_key
    if link.inward_label in INWARD_RELATIONS:
        yield link.outward_key, link.inward_key


def q10(snapshot: Snapshot, spec: Registered) -> Answer:
    """The people assigned to source issues of a relation to an issue of another assignee.

    The reading: the source has outward "blocks", "depends on" or "duplicates"
    to its `outwardIssue`, or inward "supersedes" to its `inwardIssue`; both
    issues are in the scope with qualifying, different assignees. Sources: one
    step listing the source and target issues, the links, and one flat list of
    the source and target assignees (your ruling of 2026-11-02).
    """
    assignees = assignees_in_scope(snapshot, spec)
    relations = tuple(
        (source, target, link)
        for link in snapshot.links
        for source, target in _relations(link)
        if source in assignees and target in assignees and assignees[source] != assignees[target]
    )
    sources = step(
        {
            "issue": {key for source, target, _ in relations for key in (source, target)},
            "issue_link": {link.link_id for _, _, link in relations},
            "person": {
                assignees[key] for source, target, _ in relations for key in (source, target)
            },
        }
    )
    people = {assignees[source] for source, _, _ in relations}
    return set_answer("person", people, (sources,) if relations else ())


# Q11: path_connection, set


ROLE_FIELDS = ("fields.assignee.key", "fields.reporter.key", "fields.creator.key")


def q11(snapshot: Snapshot, spec: Registered) -> Answer:
    """The people sharing an issue through any role, and the projects of those issues.

    The reading: the roles are assignee, reporter, creator and the author of a
    positive time entry, none empty or flagged; two distinct people holding
    roles on one issue are connected. Sources: one step listing the shared
    issues and the entries that establish a logging role on them (your ruling
    of 2026-11-02).
    """
    entries_on: dict[str, list[TimeEntry]] = {}
    for entry in scope_entries(snapshot, spec):
        if entry.author_key is not None:
            entries_on.setdefault(entry.issue_key, []).append(entry)
    shared: list[Issue] = []
    people: set[str] = set()
    for issue in in_scope(snapshot, spec):
        roles = {role_person(optional(issue, path), snapshot.flagged) for path in ROLE_FIELDS}
        roles |= {entry.author_key for entry in entries_on.get(issue.key, ())}
        roles.discard(None)
        if len(roles) >= 2:
            shared.append(issue)
            people |= {person for person in roles if person is not None}
    sources = step(
        {
            "issue": [issue.key for issue in shared],
            "time_entry": [
                entry.history_id for issue in shared for entry in entries_on.get(issue.key, ())
            ],
        }
    )
    ids = (
        *(typed("person", person) for person in people),
        *(typed("project", project) for project in {issue.project_key for issue in shared}),
    )
    return typed_set_answer(ids, (sources,) if shared else ())


# Q12: path_connection, set


def q12(snapshot: Snapshot, spec: Registered) -> Answer:
    """The parent issues whose direct sub-tasks have at least two qualifying assignees.

    The reading: parent and sub-task are both in the scope; only direct
    children count, by each child's `fields.parent.key`. Sources: one step
    listing the returned parents and their contributing sub-tasks, those child
    keys, and one flat list of their assignees (your rulings of 2026-11-02).
    """
    assignees = assignees_in_scope(snapshot, spec)
    in_projects = frozenset(issue.key for issue in in_scope(snapshot, spec))
    children: dict[str, list[str]] = {}
    for relation in snapshot.parents:
        if relation.parent_key in in_projects and relation.child_key in assignees:
            children.setdefault(relation.parent_key, []).append(relation.child_key)
    returned = {
        parent: kids
        for parent, kids in children.items()
        if len({assignees[kid] for kid in kids}) >= 2
    }
    kids = [kid for each in returned.values() for kid in each]
    sources = step(
        {
            "issue": (*returned, *kids),
            "parent": kids,
            "person": {assignees[kid] for kid in kids},
        }
    )
    return set_answer("issue", returned, (sources,) if returned else ())


# Q13 and Q14: a named parent and its direct sub-tasks


def anchor_children(snapshot: Snapshot, spec: Registered) -> tuple[str, tuple[str, ...]]:
    """The registered parent issue and its direct children, by each child's `fields.parent.key`.

    The registered scope holds them all, so a parent or child outside it stops the run.
    """
    if spec.anchor is None:
        raise EvalError("this question's registration has no anchor issue")
    in_projects = frozenset(issue.key for issue in in_scope(snapshot, spec))
    if spec.anchor not in in_projects:
        raise EvalError(f"{spec.anchor} is not an issue in the registered scope")
    children = tuple(
        relation.child_key for relation in snapshot.parents if relation.parent_key == spec.anchor
    )
    outside = sorted(set(children) - in_projects)
    if outside:
        raise EvalError(f"sub-tasks of {spec.anchor} are outside the registered scope: {outside}")
    return spec.anchor, children


def q13(snapshot: Snapshot, spec: Registered) -> Answer:
    """The direct sub-tasks of the registered parent.

    The reading: every direct child is returned, with no assignee or no logged
    time included. Assignee, status and total hours are not stored, because a
    `set` holds ids only. Sources: one step listing the parent and its
    sub-tasks, those child keys, and every sub-task's counted time entries.
    """
    parent, children = anchor_children(snapshot, spec)
    wanted = frozenset(children)
    entries = [e.history_id for e in scope_entries(snapshot, spec) if e.issue_key in wanted]
    sources = step({"issue": (parent, *children), "parent": children, "time_entry": entries})
    return set_answer("issue", children, (sources,) if children else ())


def q14(snapshot: Snapshot, spec: Registered) -> Answer:
    """Whether every direct sub-task of the registered parent has a resolution.

    The reading: the parent must have at least one direct sub-task. Sources:
    one step listing the parent and its sub-tasks and those child keys; a "no"
    has the empty step list under AC-32 (your ruling of 2026-11-02).
    """
    parent, children = anchor_children(snapshot, spec)
    if not children:
        raise EvalError(f"{parent} has no direct sub-task, which the reading requires")
    wanted = frozenset(children)
    resolved = all(
        optional(issue, "fields.resolution.name") is not None
        for issue in in_scope(snapshot, spec)
        if issue.key in wanted
    )
    sources = step({"issue": (parent, *children), "parent": children})
    return boolean_answer(resolved, (sources,) if resolved else ())


# Q15: mixed, number, seconds


def q15(snapshot: Snapshot, spec: Registered) -> Answer:
    """The logged seconds on the registered parent and its direct sub-tasks together.

    The reading: only direct children count, by each child's `fields.parent.key`;
    each entry counts once; an issue with no entry contributes zero. Sources: one
    step in every case, even if the total is 0 or a list is empty, listing the
    parent and its sub-tasks, those child keys and the counted entries (your
    rulings of 2026-11-02).
    """
    parent, children = anchor_children(snapshot, spec)
    wanted = frozenset((parent, *children))
    entries = tuple(e for e in scope_entries(snapshot, spec) if e.issue_key in wanted)
    sources = step(
        {"issue": wanted, "parent": children, "time_entry": [e.history_id for e in entries]}
    )
    return number_answer(count(sum(e.amount for e in entries)), (sources,))


# Q16: mixed, number, count


def _blocked_by(link: IssueLink) -> Iterable[Hop]:
    if link.inward_label == "is blocked by":
        yield link.outward_key, link.inward_key
    if link.outward_label == "depends on":
        yield link.inward_key, link.outward_key


def q16(snapshot: Snapshot, spec: Registered) -> Answer:
    """The open Highest priority bugs blocked by, or depending on, an unresolved issue.

    The reading: the bug has `fields.issuetype.name` "Bug", `fields.priority.name`
    "Highest" and no resolution; from the bug, inward "is blocked by" to its
    `inwardIssue` or outward "depends on" to its `outwardIssue`, and that issue
    has no resolution; both are in the scope. Each bug counts once. Sources: one
    step listing the counted bugs, their linked issues and the links; a count of 0
    has the empty step list.
    """
    issues = in_scope(snapshot, spec)
    unresolved = frozenset(
        issue.key for issue in issues if optional(issue, "fields.resolution.name") is None
    )
    bugs = frozenset(
        issue.key
        for issue in issues
        if issue.key in unresolved
        and optional(issue, "fields.issuetype.name") == "Bug"
        and optional(issue, "fields.priority.name") == "Highest"
    )
    kept = tuple(
        (bug, linked, link)
        for link in snapshot.links
        for bug, linked in _blocked_by(link)
        if bug in bugs and linked in unresolved
    )
    sources = step(
        {
            "issue": {key for bug, linked, _ in kept for key in (bug, linked)},
            "issue_link": {link.link_id for _, _, link in kept},
        }
    )
    return number_answer(count(len({bug for bug, _, _ in kept})), (sources,) if kept else ())


# Q17 and Q19: links between issues in different projects


def projects_of(snapshot: Snapshot, spec: Registered) -> dict[str, str]:
    """Each issue in the scope with its current project."""
    return {issue.key: issue.project_key for issue in in_scope(snapshot, spec)}


def cross_project_links(snapshot: Snapshot, projects: Mapping[str, str]) -> tuple[IssueLink, ...]:
    """The links with both issues in `projects`, in different projects; any type, any direction."""
    return tuple(
        link
        for link in snapshot.links
        if link.inward_key in projects
        and link.outward_key in projects
        and projects[link.inward_key] != projects[link.outward_key]
    )


# Q17: mixed, map, seconds


def q17(snapshot: Snapshot, spec: Registered) -> Answer:
    """Per person, the seconds on issues in a linked pair across projects they logged on both.

    The reading: two directly linked issues in different projects, with the same
    non bot author holding a positive entry on each; that person's entries on the
    distinct issues of their pairs are summed once each. Sources: two steps, the
    issues and links, then the returned people and those entries; a shared person
    hop lists person (your rulings of 2026-11-02).
    """
    entries = scope_entries(snapshot, spec)
    authors: dict[str, set[str]] = {}
    for entry in entries:
        if entry.author_key is not None:
            authors.setdefault(entry.issue_key, set()).add(entry.author_key)
    qualifying: dict[str, set[str]] = {}
    links: set[str] = set()
    for link in cross_project_links(snapshot, projects_of(snapshot, spec)):
        for person in authors.get(link.inward_key, set()) & authors.get(link.outward_key, set()):
            qualifying.setdefault(person, set()).update((link.inward_key, link.outward_key))
            links.add(link.link_id)
    kept = tuple(
        entry
        for entry in entries
        if entry.author_key is not None and entry.issue_key in qualifying.get(entry.author_key, ())
    )
    totals = seconds_per(kept, lambda e: str(e.author_key))
    values: Mapping[str, Number] = {person: count(total) for person, total in totals.items()}
    sources = (
        step({"issue": {key for keys in qualifying.values() for key in keys}, "issue_link": links}),
        step({"person": set(qualifying), "time_entry": [entry.history_id for entry in kept]}),
    )
    return map_answer("person", values, sources if kept else ())


# Q18: mixed, map, count


def q18(snapshot: Snapshot, spec: Registered) -> Answer:
    """The people who are reporter or assignee in at least two projects, and how many.

    The reading: both roles are combined when collecting a person's projects; an
    empty or flagged key is no person. Sources: one step listing the issues where
    a returned person holds a role, those people and the projects of those issues
    (your rulings of 2026-11-02).
    """
    held: dict[str, dict[str, str]] = {}
    for issue in in_scope(snapshot, spec):
        for path in ("fields.reporter.key", "fields.assignee.key"):
            person = role_person(optional(issue, path), snapshot.flagged)
            if person is not None:
                held.setdefault(person, {})[issue.key] = issue.project_key
    returned = {person: issues for person, issues in held.items() if len(set(issues.values())) >= 2}
    listed = {key: project for issues in returned.values() for key, project in issues.items()}
    values: Mapping[str, Number] = {
        person: count(len(set(issues.values()))) for person, issues in returned.items()
    }
    sources = step({"issue": set(listed), "person": set(returned), "project": set(listed.values())})
    return map_answer("person", values, (sources,) if returned else ())


# Q19: mixed, ranking, count


def q19(snapshot: Snapshot, spec: Registered) -> Answer:
    """The top projects by their issues directly linked to an issue in another corpus project.

    The reading: any link type, direction ignored, by link id; each issue counts
    once in its own project; highest first, cut at five, ties at the fifth kept.
    Sources: one step listing the returned projects, the links touching them and
    the issues at both ends of those links (your rulings of 2026-11-02).
    """
    projects = projects_of(snapshot, spec)
    links = cross_project_links(snapshot, projects)
    counted: dict[str, set[str]] = {}
    for link in links:
        for key in (link.inward_key, link.outward_key):
            counted.setdefault(projects[key], set()).add(key)
    groups, returned = top_groups(
        "project", {project: len(keys) for project, keys in counted.items()}, spec
    )
    kept = tuple(
        link
        for link in links
        if projects[link.inward_key] in returned or projects[link.outward_key] in returned
    )
    sources = step(
        {
            "issue": {key for link in kept for key in (link.inward_key, link.outward_key)},
            "issue_link": {link.link_id for link in kept},
            "project": returned,
        }
    )
    return ranked(groups, (sources,) if returned else ())


# Q20: mixed, ranking, seconds


def q20(snapshot: Snapshot, spec: Registered) -> Answer:
    """The top people by seconds logged in the window, among those in at least two projects.

    The reading: a positive entry by a non bot author with `created` in
    `[2009-07-01, 2009-10-01)` UTC; eligibility and the total both use only those
    entries; highest first, cut at five, ties at the fifth kept. Sources: one step
    listing the issues and entries of the returned people, those people and the
    projects of those entries (your rulings of 2026-11-02).
    """
    start, end = window(spec)
    kept = tuple(
        entry
        for entry in scope_entries(snapshot, spec)
        if entry.author_key is not None and in_interval(entry.created_utc, start, end)
    )
    logged_in: dict[str, set[str]] = {}
    for entry in kept:
        logged_in.setdefault(str(entry.author_key), set()).add(str(entry.project_key))
    eligible = {
        person: total
        for person, total in seconds_per(kept, lambda e: str(e.author_key)).items()
        if len(logged_in[person]) >= 2
    }
    groups, returned = top_groups("person", eligible, spec)
    contributing = tuple(entry for entry in kept if str(entry.author_key) in returned)
    sources = step(
        {
            "issue": {entry.issue_key for entry in contributing},
            "person": returned,
            "project": {str(entry.project_key) for entry in contributing},
            "time_entry": [entry.history_id for entry in contributing],
        }
    )
    return ranked(groups, (sources,) if returned else ())


# The registry (AC-28)

CORPUS_PROJECTS = ("BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER")

REGISTRY: Mapping[str, Registered] = types.MappingProxyType(
    {
        "Q01": Registered(
            q01,
            scope=CORPUS_PROJECTS,
            steps=(("issue",),),
            window=(
                dt.datetime(2013, 1, 1, tzinfo=dt.UTC),
                dt.datetime(2013, 4, 1, tzinfo=dt.UTC),
            ),
        ),
        "Q02": Registered(q02, scope=("CWD",), steps=(("issue",),)),
        "Q03": Registered(
            q03,
            scope=("FE",),
            steps=(("issue",),),
            window=(
                dt.datetime(2012, 4, 1, tzinfo=dt.UTC),
                dt.datetime(2012, 7, 1, tzinfo=dt.UTC),
            ),
        ),
        "Q04": Registered(q04, scope=CORPUS_PROJECTS, steps=(("issue", "time_entry"),)),
        "Q05": Registered(q05, scope=CORPUS_PROJECTS, steps=(("issue", "time_entry"),)),
        "Q06": Registered(
            q06,
            scope=CORPUS_PROJECTS,
            steps=(("issue", "time_entry"),),
            window=(
                dt.datetime(2009, 10, 1, tzinfo=dt.UTC),
                dt.datetime(2010, 1, 1, tzinfo=dt.UTC),
            ),
            cut=5,
            direction="descending",
        ),
        "Q07": Registered(
            q07,
            scope=CORPUS_PROJECTS,
            steps=(("issue", "person", "time_entry"),),
            cut=10,
            direction="descending",
        ),
        "Q08": Registered(q08, scope=CORPUS_PROJECTS, steps=(("issue", "issue_link"),), max_hops=5),
        "Q09": Registered(q09, scope=CORPUS_PROJECTS, steps=(("issue", "issue_link"),), max_hops=5),
        "Q10": Registered(q10, scope=CORPUS_PROJECTS, steps=(("issue", "issue_link", "person"),)),
        "Q11": Registered(q11, scope=CORPUS_PROJECTS, steps=(("issue", "time_entry"),)),
        "Q12": Registered(q12, scope=("CLOUD", "CLOV"), steps=(("issue", "parent", "person"),)),
        "Q13": Registered(
            q13, scope=("CLOV",), steps=(("issue", "parent", "time_entry"),), anchor="CLOV-108"
        ),
        "Q14": Registered(q14, scope=("CLOV",), steps=(("issue", "parent"),), anchor="CLOV-538"),
        "Q15": Registered(
            q15, scope=("CLOUD",), steps=(("issue", "parent", "time_entry"),), anchor="CLOUD-643"
        ),
        "Q16": Registered(q16, scope=CORPUS_PROJECTS, steps=(("issue", "issue_link"),)),
        "Q17": Registered(
            q17,
            scope=CORPUS_PROJECTS,
            steps=(("issue", "issue_link"), ("person", "time_entry")),
        ),
        "Q18": Registered(q18, scope=CORPUS_PROJECTS, steps=(("issue", "person", "project"),)),
        "Q19": Registered(
            q19,
            scope=CORPUS_PROJECTS,
            steps=(("issue", "issue_link", "project"),),
            cut=5,
            direction="descending",
        ),
        "Q20": Registered(
            q20,
            scope=CORPUS_PROJECTS,
            steps=(("issue", "person", "project", "time_entry"),),
            window=(
                dt.datetime(2009, 7, 1, tzinfo=dt.UTC),
                dt.datetime(2009, 10, 1, tzinfo=dt.UTC),
            ),
            cut=5,
            direction="descending",
        ),
    }
)
