"""`orgos eval describe`: the one look at the real data allowed before you write questions.

It runs only the AC-13 queries (one document's shape, fill rates, distinct
values, and the two logged exceptions) through the reader login, logs each
query's text to `eval/exploration.log` before running it (AC-14), and writes
`eval/data-description.md` with the two field lists the checker reads
(AC-15 to AC-17). Mongo only filters and projects; every count is made here,
in Python (AC-18). The functions above `describe` are pure; `describe` is the
shell.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymongo.errors import PyMongoError

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.entries import TimeEntry, rebuild_entries
from orgos.eval.fields import (
    FieldClasses,
    PathSummary,
    categorical_paths,
    field_classes,
    filled_paths,
    kind,
    summarize,
    walked_values,
)
from orgos.mongostore import describe as error_text
from orgos.mongostore.connect import reader_client, secrets
from orgos.mongostore.dump import DATABASE

CORPUS_FILE = Path("spikes/0001-time-data-coverage/results/corpus.json")
DESCRIPTION_NAME = "data-description.md"
LOG_NAME = "exploration.log"

# The value lists AC-13 always writes, whatever their class.
NAMED_VALUE_LISTS = frozenset(
    {
        "fields.issuetype.name",
        "fields.status.name",
        "fields.resolution.name",
        "fields.priority.name",
        "fields.issuelinks[].type.name",
    }
)
# Negative entries are counted per project, never per person, so the person
# field only fills `TimeEntry.author_key` and is not read.
PERSON_FIELD = "key"

WALK_PROJECTION = {
    "_id": 0,
    "key": 1,
    "id": 1,
    "fields": 1,
    "changelog.histories.id": 1,
    "changelog.histories.created": 1,
    "changelog.histories.author.key": 1,
    "changelog.histories.items": 1,
}
LINKS_PROJECTION = {
    "_id": 0,
    "key": 1,
    "fields.project.key": 1,
    "fields.issuelinks.id": 1,
    "fields.issuelinks.inwardIssue.key": 1,
    "fields.issuelinks.outwardIssue.key": 1,
    "fields.parent.key": 1,
}
ENTRIES_PROJECTION = {
    "_id": 0,
    "key": 1,
    "fields.project.key": 1,
    "changelog.histories.id": 1,
    "changelog.histories.created": 1,
    "changelog.histories.author.key": 1,
    "changelog.histories.items": 1,
}


@dataclass(frozen=True)
class Corpus:
    """The corpus projects and their collection, from the spike's `corpus.json`."""

    collection: str
    projects: tuple[str, ...]


@dataclass(frozen=True)
class Query:
    """One read the describe command runs, with the text it logs."""

    category: str
    method: str
    collection: str
    filter: Mapping[str, Any]
    projection: Mapping[str, int] | None = None

    @property
    def text(self) -> str:
        """The query as mongosh would run it: no output, URI or user name."""
        args = [json.dumps(self.filter)]
        if self.projection is not None:
            args.append(json.dumps(self.projection))
        target = f'db.getSiblingDB("{DATABASE}").getCollection("{self.collection}")'
        return f"{target}.{self.method}({', '.join(args)})"


@dataclass(frozen=True)
class Queries:
    """The five reads, in the order they run."""

    shape: Query
    fill_rate: Query
    distinct_values: Query
    links_parents: Query
    negative_entries: Query

    def all(self) -> tuple[Query, ...]:
        """Every query, in run order."""
        return (
            self.shape,
            self.fill_rate,
            self.distinct_values,
            self.links_parents,
            self.negative_entries,
        )


@dataclass(frozen=True)
class LinkRow:
    """One issue's own links and parent, read without following any of them."""

    project: str
    key: str
    links: tuple[tuple[str, str | None], ...]
    parent: str | None


@dataclass(frozen=True)
class Kept:
    """Per project: issue links (by link id) and parent relations kept and dropped (AC-24)."""

    links_kept: int
    links_dropped: int
    parents_kept: int
    parents_dropped: int


@dataclass(frozen=True)
class Negative:
    """Per project: how many time entries are below 0, and their seconds."""

    count: int
    seconds: int


@dataclass(frozen=True)
class Description:
    """Everything `eval/data-description.md` shows, computed before it is written."""

    corpus: Corpus
    written_at: dt.datetime
    shape: tuple[tuple[str, str], ...]
    classes: FieldClasses
    value_lists: tuple[tuple[str, tuple[str, ...]], ...]
    issues: Mapping[str, int]
    filled: Mapping[str, Mapping[str, int]]
    kept: Mapping[str, Kept]
    negative: Mapping[str, Negative]


@dataclass(frozen=True)
class DescribeResult:
    """What the command reports: the files and how many paths landed on each list."""

    description: Path
    log: Path
    usable: int
    free_text: int
    queries: int

    @property
    def summary(self) -> str:
        """One line for the terminal."""
        return (
            f"describe: wrote {self.description} ({self.usable} usable paths, "
            f"{self.free_text} free text paths); logged {self.queries} queries to {self.log}"
        )


# Inputs and queries


def read_corpus(path: Path) -> Corpus:
    """The `group` and `collection` of the spike's `corpus.json`."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalError(f"{path} not found") from None
    except (OSError, ValueError) as error:
        raise EvalError(f"{path} cannot be read: {error}") from None
    group, collection = raw.get("group"), raw.get("collection")
    if not isinstance(collection, str) or not isinstance(group, list) or not group:
        raise EvalError(f"{path} has no collection or no group of projects")
    if not all(isinstance(key, str) and key for key in group):
        raise EvalError(f"{path} has a project key that is not text")
    return Corpus(collection, tuple(group))


def corpus_queries(corpus: Corpus) -> Queries:
    """The AC-13 reads over the corpus issues, by current `fields.project.key` (AC-24)."""
    issues = {"fields.project.key": {"$in": list(corpus.projects)}}
    name = corpus.collection
    return Queries(
        shape=Query("shape", "findOne", name, issues),
        fill_rate=Query("fill_rate", "find", name, issues, WALK_PROJECTION),
        distinct_values=Query("distinct_values", "find", name, issues, WALK_PROJECTION),
        links_parents=Query("exception:links_parents", "find", name, issues, LINKS_PROJECTION),
        negative_entries=Query(
            "exception:negative_entries", "find", name, issues, ENTRIES_PROJECTION
        ),
    )


def log_block(query: Query, at: dt.datetime) -> str:
    """The AC-14 block for one query: its UTC time and category, then its text."""
    stamp = at.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"## {stamp} · {query.category}\n\n```\n{query.text}\n```\n\n"


# The counts, made in Python


def project_of(issue: Mapping[str, Any]) -> str:
    """An issue's current `fields.project.key`; an issue without one stops the run."""
    project = (issue.get("fields") or {}).get("project") or {}
    key = project.get("key") if isinstance(project, Mapping) else None
    if not isinstance(key, str) or not key:
        raise EvalError(f"{issue.get('key')}: the issue has no fields.project.key")
    return key


def document_shape(document: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """The type of each top level member and of the changelog's history and item members.

    Types only, never a value.
    """
    if document is None:
        return ()

    def type_name(value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, Mapping):
            return "object"
        if isinstance(value, list | tuple):
            inner = sorted({type_name(element) for element in value})
            return f"array of {', '.join(inner)}" if inner else "array (empty)"
        return kind(value)

    def members(prefix: str, value: object) -> Iterator[tuple[str, str]]:
        if isinstance(value, Mapping):
            for name in sorted(value):
                yield f"{prefix}{name}", type_name(value[name])

    def first(value: object) -> object:
        return value[0] if isinstance(value, list | tuple) and value else None

    changelog = document.get("changelog")
    histories = changelog.get("histories") if isinstance(changelog, Mapping) else None
    history = first(histories)
    items = history.get("items") if isinstance(history, Mapping) else None
    return (
        *members("", document),
        *members("changelog.", changelog),
        *members("changelog.histories[].", history),
        *members("changelog.histories[].items[].", first(items)),
    )


def fill_counts(
    rows: Iterable[tuple[str, frozenset[str]]],
) -> tuple[Mapping[str, int], Mapping[str, Mapping[str, int]]]:
    """Issues per project, and per path the issues per project where it is filled."""
    issues: Counter[str] = Counter()
    filled: dict[str, Counter[str]] = {}
    for project, paths in rows:
        issues[project] += 1
        for path in paths:
            filled.setdefault(path, Counter())[project] += 1
    return dict(issues), {path: dict(counts) for path, counts in filled.items()}


def link_row(issue: Mapping[str, Any]) -> LinkRow:
    """One issue's link ids with their other end, and its parent key, as stored on it."""
    key = issue.get("key")
    if not isinstance(key, str) or not key:
        raise EvalError("an issue has no key")
    fields = issue.get("fields") or {}
    links: list[tuple[str, str | None]] = []
    for link in fields.get("issuelinks") or ():
        link_id = link.get("id") if isinstance(link, Mapping) else None
        if link_id is None or link_id == "":
            raise EvalError(f"{key}: an issue link has no id")
        other = None
        for end in ("inwardIssue", "outwardIssue"):
            other_key = (link.get(end) or {}).get("key")
            if isinstance(other_key, str) and other_key:
                other = other_key
        links.append((str(link_id), other))
    parent = (fields.get("parent") or {}).get("key")
    return LinkRow(
        project_of(issue),
        key,
        tuple(links),
        parent if isinstance(parent, str) and parent else None,
    )


def kept_and_dropped(rows: Iterable[LinkRow]) -> Mapping[str, Kept]:
    """Per project, links and parents whose other issue is or is not a corpus issue (AC-24).

    The corpus keys are the issues read in the same pass, so membership is by
    current project. A link counts once per project by its id.
    """
    rows = tuple(rows)
    corpus_keys = frozenset(row.key for row in rows)
    kept: dict[str, set[str]] = {}
    dropped: dict[str, set[str]] = {}
    parents: dict[str, Counter[str]] = {}
    for row in rows:
        kept.setdefault(row.project, set())
        dropped.setdefault(row.project, set())
        parents.setdefault(row.project, Counter())
        for link_id, other in row.links:
            (kept if other in corpus_keys else dropped)[row.project].add(link_id)
        if row.parent is not None:
            parents[row.project]["kept" if row.parent in corpus_keys else "dropped"] += 1
    return {
        project: Kept(
            len(kept[project]),
            len(dropped[project]),
            parents[project]["kept"],
            parents[project]["dropped"],
        )
        for project in kept
    }


def negative_entries(entries: Iterable[TimeEntry]) -> Mapping[str, Negative]:
    """Per project, the count and seconds of entries below 0, never grouped by person."""
    count: Counter[str] = Counter()
    seconds: Counter[str] = Counter()
    for entry in entries:
        if entry.amount < 0 and entry.project_key is not None:
            count[entry.project_key] += 1
            seconds[entry.project_key] += entry.amount
    return {project: Negative(count[project], seconds[project]) for project in count}


def value_lists(
    summaries: Mapping[str, PathSummary],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The distinct values of the named categorical paths and of every path classed categorical."""
    paths = sorted(NAMED_VALUE_LISTS | set(categorical_paths(summaries)))
    return tuple(
        (path, tuple(sorted(summaries[path].distinct)) if path in summaries else ())
        for path in paths
    )


# The file


def percent(filled: int, total: int) -> str:
    """A fill rate for display: exact at 0 and 100, never rounded to either."""
    if total == 0 or filled == 0:
        return "0"
    if filled == total:
        return "100"
    share = 100 * filled / total
    if share < 0.1:
        return "<0.1"
    if share > 99.9:
        return ">99.9"
    return f"{share:.1f}"


def render(description: Description) -> str:
    """The text of `eval/data-description.md`."""
    projects = description.corpus.projects
    stamp = description.written_at.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Data description",
        "",
        f"Written by `orgos eval describe` at {stamp} (spec 0005, AC-13 to AC-17), from the "
        "reader login only. The class of every path below was computed once, here, and is "
        "never recomputed. Do not edit this file by hand.",
        "",
        "## Corpus projects",
        "",
        f"The {len(projects)} projects of `{CORPUS_FILE}` (`group`), in the "
        f"`{description.corpus.collection}` collection: {', '.join(projects)}. An issue "
        "belongs to the project in its current `fields.project.key`.",
        "",
        "## Entities",
        "",
        "- Issue: one document per issue, identified by `key`.",
        "- Project: `fields.project.key`.",
        "- Person: the `.key` of a user object, for example `fields.assignee.key` or "
        "`changelog.histories[].author.key`.",
        "- Time entry: rebuilt from the changelog, since there is no worklog field. Each "
        "changelog history holding a `timespent` item of fieldtype `jira` is one entry: its id "
        "is `changelog.histories[].id`, its person `changelog.histories[].author.key`, its time "
        "`changelog.histories[].created`, and its amount `to` minus `from` in seconds.",
        "- Issue link: `fields.issuelinks[]`, identified by `fields.issuelinks[].id`.",
        "- Parent relation: the child's `fields.parent.key`.",
        "",
        "## Document shape",
        "",
        "The type of each member of one corpus issue (the first the shape query returned), "
        "and of its first changelog history and item. Types only.",
        "",
        *(f"- `{path}`: {type_name}" for path, type_name in description.shape),
        "",
        "## Usable fields",
        "",
        "Every path a question may cite in `fields`, with the AC-17 rule that made it usable.",
        "",
        *(f"- `{item.path}` ({item.reason})" for item in description.classes.usable),
        "",
        "## Free text, not usable",
        "",
        *(f"- `{item.path}` ({item.reason})" for item in description.classes.free_text),
        "",
        "## Value lists",
        "",
        "Distinct values of the issue type, status, resolution, priority and link type names, "
        "and of every path classed categorical, pooled over the corpus projects. No counts.",
        "",
    ]
    for path, values in description.value_lists:
        lines += [f"### `{path}`", ""]
        lines += [f"- {json.dumps(value, ensure_ascii=False)}" for value in values] or [
            "- (no values)"
        ]
        lines.append("")
    lines += [
        "## Fill rates per project",
        "",
        "Percent of the project's issues where the path holds a value that is not null, "
        '`""` or `[]` (AC-15). A changelog path is filled when any history of the issue fills it.',
        "",
        f"| Path | {' | '.join(projects)} | All |",
        f"|---|{'---|' * (len(projects) + 1)}",
    ]
    total = sum(description.issues.get(project, 0) for project in projects)
    classes = description.classes
    listed = sorted(item.path for item in classes.usable + classes.free_text)
    for path in listed:
        counts = description.filled.get(path, {})
        cells = [percent(counts.get(p, 0), description.issues.get(p, 0)) for p in projects]
        cells.append(percent(sum(counts.get(p, 0) for p in projects), total))
        lines.append(f"| `{path}` | {' | '.join(cells)} |")
    lines += [
        "",
        "## Issue links and parents kept or dropped",
        "",
        "Per project, read from each issue's own fields without following any link (AC-24). "
        "Kept means the other issue is a corpus issue. A link counts once per project, by its "
        "id; a link between two corpus projects counts in each.",
        "",
        "| Project | Links kept | Links dropped | Parents kept | Parents dropped |",
        "|---|---|---|---|---|",
    ]
    for project in projects:
        row = description.kept.get(project, Kept(0, 0, 0, 0))
        lines.append(
            f"| {project} | {row.links_kept} | {row.links_dropped} | "
            f"{row.parents_kept} | {row.parents_dropped} |"
        )
    lines += [
        "",
        "## Negative time entries",
        "",
        "Per project, the time entries whose amount is below 0 (decreases or removals), and "
        "their total seconds. Not grouped by person.",
        "",
        "| Project | Entries | Seconds |",
        "|---|---|---|",
    ]
    for project in projects:
        negative = description.negative.get(project, Negative(0, 0))
        lines.append(f"| {project} | {negative.count} | {negative.seconds} |")
    return "\n".join(lines) + "\n"


# The shell


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def describe(
    settings: Settings,
    out_dir: Path = Path("eval"),
    corpus_file: Path = CORPUS_FILE,
    clock: Callable[[], dt.datetime] = _utc_now,
) -> DescribeResult:
    """Run the AC-13 reads through the reader login and write `data-description.md`.

    Each query's AC-14 block is appended to `exploration.log` before it runs.
    The description is written only once every query has finished.
    """
    corpus = read_corpus(corpus_file)
    queries = corpus_queries(corpus)
    log_path = out_dir / LOG_NAME
    description_path = out_dir / DESCRIPTION_NAME

    def logged(query: Query) -> None:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(log_block(query, clock()))

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with reader_client(settings) as client:
            collection = client[DATABASE][corpus.collection]

            def find(query: Query) -> Iterator[Mapping[str, Any]]:
                logged(query)
                yield from collection.find(dict(query.filter), dict(query.projection or {}))

            logged(queries.shape)
            shape = document_shape(collection.find_one(dict(queries.shape.filter)))
            issues, filled = fill_counts(
                (project_of(issue), filled_paths(issue)) for issue in find(queries.fill_rate)
            )
            summaries = summarize(
                (pair for issue in find(queries.distinct_values) for pair in walked_values(issue)),
                keep=NAMED_VALUE_LISTS,
            )
            kept = kept_and_dropped(link_row(issue) for issue in find(queries.links_parents))
            negative = negative_entries(
                entry
                for issue in find(queries.negative_entries)
                for entry in rebuild_entries(issue, PERSON_FIELD)
            )
        classes = field_classes(summaries)
        description = Description(
            corpus=corpus,
            written_at=clock(),
            shape=shape,
            classes=classes,
            value_lists=value_lists(summaries),
            issues=issues,
            filled=filled,
            kept=kept,
            negative=negative,
        )
        description_path.write_text(render(description), encoding="utf-8")
    except PyMongoError as error:
        raise EvalError(f"describe failed: {error_text(error, secrets(settings))}") from None
    except OSError as error:
        raise EvalError(f"describe could not write to {out_dir}: {error.strerror}") from None
    return DescribeResult(
        description_path,
        log_path,
        len(classes.usable),
        len(classes.free_text),
        len(queries.all()),
    )
