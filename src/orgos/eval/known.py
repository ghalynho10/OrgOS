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
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from orgos.eval import EvalError
from orgos.eval.models import Answer, Number, SourceKind
from orgos.eval.rules import in_interval, utc_time
from orgos.eval.snapshot import Issue, Snapshot
from orgos.eval.writer import Direction, count, map_answer, step

Window = tuple[dt.datetime, dt.datetime]


@dataclass(frozen=True)
class Registered:
    """One question's function and the constants its reading note fixes (AC-28)."""

    function: Callable[[Snapshot, Registered], Answer]
    # The projects the reading's population names, by current `fields.project.key`.
    scope: tuple[str, ...]
    # The kinds each source step lists, one entry per step (AC-32).
    steps: tuple[tuple[SourceKind, ...], ...]
    # A half open UTC date window `[start, end)` (AC-25), when the reading sets one.
    window: Window | None = None
    cut: int | None = None
    direction: Direction | None = None
    max_hops: int | None = None


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


def window(spec: Registered) -> Window:
    """The registered date window; a function that needs one stops without it."""
    if spec.window is None:
        raise EvalError("this question's registration has no date window")
    return spec.window


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
    }
)
