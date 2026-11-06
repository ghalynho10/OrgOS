"""The figure gate and the load consistency check (spec 0009 AC-15 to AC-17).

The gate compares the exact figures of your scans with what the built tables
give, after dbt and the load consistency check have passed. The constants below
exist only in spec text; every other expected value is read from a committed
file. A figure that differs is never adjusted here: it reopens the model under
spec 0006 AC-23. The rules are rebuilt from the specs and never read
`eval/answers.json` or the eval code, so a disagreement is a real finding.

`load_consistency` is the check features 11 to 13 reuse: `raw.jira_issue` and
every table of `model_int` and `model` hold exactly one load ID, equal to the
latest completed dlt load.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

from orgos.ingest import CORPUS_PROJECTS

# Spec 0006, Follow-up line 190 (your scans), and AC-26.
DOCUMENTS_IN_RAW = 87_839
# Spec 0006 AC-26 and line 190: 87,839 documents minus 4 collapsed copies.
ISSUE_ROWS = 87_835
# Spec 0006 AC-26: exactly the 7 corpus projects.
PROJECT_ROWS = 7
# Spec 0006 AC-13 and line 190: staged links by link ID, kept and dropped.
STAGED_LINKS = 43_462
KEPT_LINKS = 21_721
DROPPED_LINKS = 21_741
# Spec 0006 line 190: links pointing at a corpus prefix key with no issue.
CORPUS_PREFIX_LINKS_WITHOUT_ISSUE = 1_121
# Spec 0006 line 190: time entries above 0.
POSITIVE_ENTRIES = 7_923
# Spec 0006 AC-15 and eval/data-description.md, Negative time entries.
NEGATIVE_ENTRIES = 70
NEGATIVE_SECONDS = -1_356_720
# Spec 0006 line 190: none of each.
ZERO = 0
# Spec 0006 AC-17 and spec 0005 AC-21: the flagged bot set is empty for Jira.
FLAGGED_ACCOUNTS: tuple[str, ...] = ()
# Spec 0005 AC-27 and spec 0006 AC-33: the net check set.
NET_CHECK_SET: tuple[str, ...] = ("FE-4996",)

CORPUS_FILE = "spikes/0001-time-data-coverage/results/corpus.json"
KNOWN_ANSWERS_RECORD = "results/known_answers/run_record.json"


@dataclass(frozen=True)
class Figure:
    """One gate figure: its name, the value expected, and where that value comes from."""

    name: str
    expected: Any
    source: str


@dataclass(frozen=True)
class FigureResult:
    """One gate figure compared: expected beside found."""

    name: str
    expected: Any
    found: Any
    ok: bool
    source: str

    def record(self) -> dict[str, Any]:
        """The run record object for this figure."""
        return {
            "name": self.name,
            "expected": self.expected,
            "found": self.found,
            "ok": self.ok,
            "source": self.source,
        }


def expected_figures(
    corpus: Mapping[str, Any], known_answers: Mapping[str, Any]
) -> tuple[Figure, ...]:
    """Every gate figure with its expected value, from the constants and the two files."""
    projects = corpus["projects"]
    return (
        Figure("documents in raw", DOCUMENTS_IN_RAW, "spec 0006 line 190; Mongo count (AC-5)"),
        Figure("issue rows", ISSUE_ROWS, "spec 0006 AC-26 and line 190"),
        Figure("project rows", PROJECT_ROWS, "spec 0006 AC-26"),
        Figure("staged links by link ID", STAGED_LINKS, "spec 0006 AC-13 and line 190"),
        Figure("kept links", KEPT_LINKS, "spec 0006 AC-13 and line 190"),
        Figure("dropped links", DROPPED_LINKS, "spec 0006 AC-13 and line 190"),
        Figure(
            "links pointing at a corpus prefix key with no issue",
            CORPUS_PREFIX_LINKS_WITHOUT_ISSUE,
            "spec 0006 line 190",
        ),
        Figure("positive time entries", POSITIVE_ENTRIES, "spec 0006 line 190"),
        Figure(
            "entries below 0",
            NEGATIVE_ENTRIES,
            "spec 0006 AC-15; eval/data-description.md",
        ),
        Figure(
            "seconds of entries below 0",
            NEGATIVE_SECONDS,
            "spec 0006 AC-15; eval/data-description.md",
        ),
        Figure("entries with no author", ZERO, "spec 0006 line 190"),
        Figure("empty strings in used columns", ZERO, "spec 0006 line 190"),
        Figure("kept links on one document only", ZERO, "spec 0006 line 190"),
        Figure("self links", ZERO, "spec 0006 line 190"),
        Figure("link entries with neither side", ZERO, "spec 0006 line 190"),
        Figure("persons with two values", ZERO, "spec 0006 line 190"),
        Figure("projects with two values", ZERO, "spec 0006 line 190"),
        Figure(
            "positive seconds per project",
            {project["key"]: project["positive_seconds"] for project in projects},
            f"file: {CORPUS_FILE}",
        ),
        Figure(
            "distinct persons per project",
            {project["key"]: project["people"] for project in projects},
            f"file: {CORPUS_FILE}",
        ),
        Figure(
            "flagged accounts",
            list(FLAGGED_ACCOUNTS),
            "spec 0006 AC-17; compared with the known answers record",
        ),
        Figure(
            "collapsed keys",
            collapsed_record(known_answers["collapsed_documents"]),
            f"file: {KNOWN_ANSWERS_RECORD}, collapsed_documents",
        ),
        Figure("net check set", list(NET_CHECK_SET), "spec 0005 AC-27, spec 0006 AC-33"),
        Figure(
            "net check set of the known answers",
            list(NET_CHECK_SET),
            f"spec 0005 AC-27; compared with {KNOWN_ANSWERS_RECORD} reconciliation",
        ),
    )


def collapsed_record(collapsed: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapsed keys as `{key: {kept, dropped (sorted)}}`, sorted by key."""
    return {
        key: {"kept": collapsed[key]["kept"], "dropped": sorted(collapsed[key]["dropped"])}
        for key in sorted(collapsed)
    }


def compare_figures(
    expected: Sequence[Figure], found: Mapping[str, Any]
) -> tuple[FigureResult, ...]:
    """Each figure's expected value beside the found one; a figure not found fails."""
    return tuple(
        FigureResult(
            name=figure.name,
            expected=figure.expected,
            found=found.get(figure.name),
            ok=figure.name in found and found[figure.name] == figure.expected,
            source=figure.source,
        )
        for figure in expected
    )


def gate_message(results: Iterable[FigureResult]) -> str | None:
    """Every differing figure together, or None when all match."""
    differing = [result for result in results if not result.ok]
    if not differing:
        return None
    lines = [f"figure gate failed: {len(differing)} figure(s) differ"]
    lines.extend(
        f"  {result.name}: expected {result.expected!r}, found {result.found!r} ({result.source})"
        for result in differing
    )
    lines.append(
        "Nothing is adjusted to pass; a differing figure reopens the model (spec 0006 AC-23)."
    )
    return "\n".join(lines)


# The SQL behind each count figure. Every count is of distinct natural IDs over
# the collapsed documents (spec 0009, What each figure counts).
_COUNT_SQL: dict[str, str] = {
    "documents in raw": "SELECT count(*) FROM raw.jira_issue",
    "issue rows": "SELECT count(*) FROM model_int.issue",
    "project rows": "SELECT count(*) FROM model_int.project",
    "staged links by link ID": "SELECT count(DISTINCT link_id) FROM stg.stg_link_entry",
    "kept links": "SELECT count(*) FROM model_int.issue_link",
    "dropped links": """
        SELECT count(DISTINCT entry.link_id) FROM stg.stg_link_entry AS entry
        WHERE NOT EXISTS (SELECT 1 FROM model_int.issue WHERE issue_key = entry.src_id)
           OR NOT EXISTS (SELECT 1 FROM model_int.issue WHERE issue_key = entry.dst_id)
    """,
    # A corpus prefix key: the text before its last hyphen is a corpus project.
    # A diagnostic only, never a selection rule (spec 0006 AC-26).
    "links pointing at a corpus prefix key with no issue": """
        SELECT count(DISTINCT entry.link_id) FROM stg.stg_link_entry AS entry
        CROSS JOIN LATERAL (VALUES (entry.src_id), (entry.dst_id)) AS side (key)
        WHERE NOT EXISTS (SELECT 1 FROM model_int.issue WHERE issue_key = side.key)
          AND substring(side.key FROM '^(.*)-[^-]*$') = ANY(%(projects)s)
    """,
    "positive time entries": "SELECT count(*) FROM model_int.logged_time WHERE seconds > 0",
    "entries below 0": "SELECT count(*) FROM model_int.logged_time WHERE seconds < 0",
    "seconds of entries below 0": """
        SELECT coalesce(sum(seconds), 0) FROM model_int.logged_time WHERE seconds < 0
    """,
    "entries with no author": """
        SELECT count(DISTINCT (issue_key, history_id)) FROM stg.stg_time_history
        WHERE author_key IS NULL
    """,
    # The source paths of the spec 0006 tables that feed a model_int column.
    "empty strings in used columns": """
        WITH used (value) AS (
            SELECT used.value FROM stg.stg_issue_doc AS doc
            CROSS JOIN LATERAL (VALUES
                (doc.key), (doc.fields ->> 'created'), (doc.fields ->> 'resolutiondate'),
                (doc.fields ->> 'duedate'), (doc.fields ->> 'timeoriginalestimate'),
                (doc.fields -> 'resolution' ->> 'name'), (doc.fields -> 'issuetype' ->> 'name'),
                (doc.fields -> 'priority' ->> 'name'), (doc.fields -> 'status' ->> 'name'),
                (doc.fields -> 'project' ->> 'key'), (doc.fields -> 'project' ->> 'name'),
                (doc.fields -> 'assignee' ->> 'key'), (doc.fields -> 'assignee' ->> 'name'),
                (doc.fields -> 'reporter' ->> 'key'), (doc.fields -> 'reporter' ->> 'name'),
                (doc.fields -> 'creator' ->> 'key'), (doc.fields -> 'creator' ->> 'name'),
                (doc.fields -> 'parent' ->> 'key')
            ) AS used (value)
            UNION ALL
            SELECT used.value FROM stg.stg_issue_doc AS doc
            CROSS JOIN LATERAL jsonb_array_elements(nullif(doc.fields -> 'fixVersions', 'null'))
                AS fix (version)
            CROSS JOIN LATERAL (VALUES (fix.version ->> 'id'), (fix.version ->> 'name'))
                AS used (value)
            UNION ALL
            SELECT used.value FROM stg.stg_link_entry AS entry
            CROSS JOIN LATERAL (VALUES
                (entry.link_id), (entry.link_type_name), (entry.outward_label),
                (entry.inward_label), (entry.outward_issue_key), (entry.inward_issue_key)
            ) AS used (value)
            UNION ALL
            SELECT used.value FROM (
                SELECT DISTINCT issue_key, history_id, author_key, created
                FROM stg.stg_time_history
            ) AS history
            CROSS JOIN LATERAL (VALUES
                (history.history_id), (history.author_key), (history.created)
            ) AS used (value)
        )
        SELECT count(*) FROM used WHERE value = ''
    """,
    "kept links on one document only": """
        SELECT count(*) FROM (
            SELECT link.link_id FROM model_int.issue_link AS link
            INNER JOIN stg.stg_link_entry AS entry ON entry.link_id = link.link_id
            GROUP BY link.link_id
            HAVING count(DISTINCT entry.holder_key) = 1
        ) AS one_sided
    """,
    "self links": """
        SELECT count(DISTINCT link_id) FROM stg.stg_link_entry WHERE src_id = dst_id
    """,
    "link entries with neither side": """
        SELECT count(*) FROM stg.stg_link_entry
        WHERE outward_issue_key IS NULL AND inward_issue_key IS NULL
    """,
    # A person key with more than one (login_name, is_active) among role objects.
    "persons with two values": """
        SELECT count(*) FROM (
            SELECT role.person ->> 'key' FROM stg.stg_issue_doc AS doc
            CROSS JOIN LATERAL (VALUES
                (doc.fields -> 'assignee'), (doc.fields -> 'reporter'), (doc.fields -> 'creator')
            ) AS role (person)
            WHERE role.person ->> 'key' IS NOT NULL
            GROUP BY role.person ->> 'key'
            HAVING count(DISTINCT (role.person ->> 'name', role.person ->> 'active')) > 1
        ) AS two_values
    """,
    "projects with two values": """
        SELECT count(*) FROM (
            SELECT fields -> 'project' ->> 'key' FROM stg.stg_issue_doc
            GROUP BY fields -> 'project' ->> 'key'
            HAVING count(DISTINCT fields -> 'project' ->> 'name') > 1
        ) AS two_values
    """,
}

# Per project, entries above 0 by the current project of the entry's issue.
_PER_PROJECT_SQL = """
    SELECT in_project.dst_id, sum(entry.seconds)::bigint, count(DISTINCT entry.src_id)
    FROM model_int.logged_time AS entry
    INNER JOIN model_int.in_project AS in_project ON in_project.src_id = entry.dst_id
    WHERE entry.seconds > 0
    GROUP BY in_project.dst_id
"""

_COLLAPSED_SQL = """
    SELECT key, kept_mongo_id, dropped_mongo_id FROM stg.stg_issue_doc_dropped
    ORDER BY key, dropped_mongo_id
"""

# Spec 0005 AC-27: corpus issues with at least one entry or a timespent above
# 0 (null read as 0, no entry read as a sum of 0) whose signed entry sum
# differs from timespent, read from stg (spec 0006 AC-33).
_NET_CHECK_SQL = """
    WITH entry_sum AS (
        SELECT dst_id AS issue_key, sum(seconds) AS seconds
        FROM model_int.logged_time GROUP BY dst_id
    ),
    issue AS (
        SELECT key AS issue_key, coalesce((fields ->> 'timespent')::bigint, 0) AS timespent
        FROM stg.stg_issue_doc
    )
    SELECT issue.issue_key FROM issue
    LEFT JOIN entry_sum ON entry_sum.issue_key = issue.issue_key
    WHERE (entry_sum.issue_key IS NOT NULL OR issue.timespent > 0)
      AND coalesce(entry_sum.seconds, 0) <> issue.timespent
    ORDER BY issue.issue_key
"""

# The project of the issue that holds each dropped link, from stg.
_DROPPED_PER_PROJECT_SQL = """
    SELECT doc.fields -> 'project' ->> 'key', count(DISTINCT entry.link_id)
    FROM stg.stg_link_entry AS entry
    INNER JOIN stg.stg_issue_doc AS doc ON doc.key = entry.holder_key
    WHERE NOT EXISTS (SELECT 1 FROM model_int.issue_link WHERE link_id = entry.link_id)
    GROUP BY 1
    ORDER BY 1
"""


@dataclass(frozen=True)
class FoundFigures:
    """What the gate read from the tables and the known answers record."""

    values: Mapping[str, Any]
    collapsed: Mapping[str, Any]
    dropped_links_per_project: Mapping[str, int]
    net_check: tuple[str, ...]


def _scalar(conn: psycopg.Connection, query: str) -> int:
    row = conn.execute(query, {"projects": list(CORPUS_PROJECTS)}).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def find_figures(conn: psycopg.Connection, known_answers: Mapping[str, Any]) -> FoundFigures:
    """Read every gate figure from the built tables (as the owner) and the known answers record."""
    values: dict[str, Any] = {name: _scalar(conn, query) for name, query in _COUNT_SQL.items()}

    per_project = {key: (0, 0) for key in CORPUS_PROJECTS}
    for project, seconds, people in conn.execute(_PER_PROJECT_SQL).fetchall():
        per_project[project] = (int(seconds), int(people))
    values["positive seconds per project"] = {
        key: seconds for key, (seconds, _) in sorted(per_project.items())
    }
    values["distinct persons per project"] = {
        key: people for key, (_, people) in sorted(per_project.items())
    }

    values["flagged accounts"] = list(known_answers["flagged_accounts"])

    collapsed: dict[str, dict[str, Any]] = {}
    for key, kept, dropped in conn.execute(_COLLAPSED_SQL).fetchall():
        collapsed.setdefault(key, {"kept": kept, "dropped": []})["dropped"].append(dropped)
    values["collapsed keys"] = collapsed_record(collapsed)

    net_check = tuple(row[0] for row in conn.execute(_NET_CHECK_SQL).fetchall())
    values["net check set"] = list(net_check)
    values["net check set of the known answers"] = list(
        known_answers["reconciliation"]["entry mismatches"]["expected"]
    )

    dropped = {
        str(project): int(count)
        for project, count in conn.execute(_DROPPED_PER_PROJECT_SQL).fetchall()
    }
    return FoundFigures(
        values=values,
        collapsed=values["collapsed keys"],
        dropped_links_per_project=dropped,
        net_check=net_check,
    )


@dataclass(frozen=True)
class TableLoad:
    """One table's row count and the distinct load IDs it holds."""

    table: str
    rows: int
    load_ids: tuple[str, ...]


@dataclass(frozen=True)
class Consistency:
    """The load consistency result: the latest completed dlt load and what differs from it."""

    latest_load_id: str | None
    tables: tuple[TableLoad, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when every table holds exactly the latest load ID."""
        return not self.problems

    def record(self) -> dict[str, Any]:
        """The run record object for this check."""
        return {
            "ok": self.ok,
            "latest_load_id": self.latest_load_id,
            "tables_checked": len(self.tables),
            "problems": list(self.problems),
        }


def consistency_problems(latest: str | None, tables: Sequence[TableLoad]) -> tuple[str, ...]:
    """Each table that is empty or holds a load ID other than exactly `latest` (AC-17)."""
    if latest is None:
        return ("raw._dlt_loads has no completed load",)
    problems = []
    for table in tables:
        if table.rows == 0:
            problems.append(f"{table.table} is empty")
        elif table.load_ids != (latest,):
            problems.append(
                f"{table.table} holds load IDs {', '.join(table.load_ids)}, expected {latest}"
            )
    return tuple(problems)


# The newest completed dlt load: status 0 rows of raw._dlt_loads.
_LATEST_LOAD_SQL = """
    SELECT load_id FROM raw._dlt_loads WHERE status = 0
    ORDER BY inserted_at DESC, load_id DESC LIMIT 1
"""
_BUILT_TABLES_SQL = """
    SELECT table_schema, table_name FROM information_schema.tables
    WHERE table_schema IN ('model_int', 'model') AND table_type = 'BASE TABLE'
    ORDER BY table_schema, table_name
"""


def _table_load(conn: psycopg.Connection, schema: str, table: str, column: str) -> TableLoad:
    row = conn.execute(
        sql.SQL("SELECT count(*), array_agg(DISTINCT {}) FROM {}").format(
            sql.Identifier(column), sql.Identifier(schema, table)
        )
    ).fetchone()
    rows, load_ids = (row[0], row[1]) if row else (0, None)
    return TableLoad(
        table=f"{schema}.{table}",
        rows=int(rows),
        load_ids=tuple(sorted(value for value in load_ids or () if value is not None)),
    )


def load_consistency(conn: psycopg.Connection) -> Consistency:
    """Check that raw and every model_int and model table hold exactly the latest dlt load ID.

    A table with no `load_id` column is named as a problem, not skipped.
    """
    latest_row = conn.execute(_LATEST_LOAD_SQL).fetchone()
    latest = str(latest_row[0]) if latest_row else None
    tables = [_table_load(conn, "raw", "jira_issue", "_dlt_load_id")]
    missing_column = []
    for schema, table in conn.execute(_BUILT_TABLES_SQL).fetchall():
        try:
            tables.append(_table_load(conn, schema, table, "load_id"))
        except psycopg.errors.UndefinedColumn:
            conn.rollback()
            missing_column.append(f"{schema}.{table} has no load_id column")
    return Consistency(
        latest_load_id=latest,
        tables=tuple(tables),
        problems=(*consistency_problems(latest, tables), *missing_column),
    )
