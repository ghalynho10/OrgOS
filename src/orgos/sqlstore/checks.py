"""What "the SQL store is in the expected state" means for the eval, written once.

Spec 0011 AC-4 and spec 0007 AC-1, AC-12, AC-27. `read_sql_state` is the only
function here that touches Postgres, and it runs as the reader login, so it
reads only `model` and the catalogs; `sql_state_problems` is pure and judges
what it returned. The shared preflight of features 12 and 13 calls both.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

import psycopg
from psycopg import sql

SCHEMA = "model"
TIMEZONE = "UTC"
# Spec 0007 AC-1: exactly these tables, no view and nothing else.
TABLES = (
    "issue",
    "project",
    "person",
    "fix_version",
    "fixed_in",
    "issue_link",
    "logged_time",
)
# Spec 0007 AC-12: (table, column, referenced table, referenced column).
ForeignKey = tuple[str, str, str, str]
FOREIGN_KEYS: tuple[ForeignKey, ...] = (
    ("issue", "project_key", "project", "project_key"),
    ("issue", "assignee_key", "person", "person_key"),
    ("issue", "reporter_key", "person", "person_key"),
    ("issue", "creator_key", "person", "person_key"),
    ("logged_time", "person_key", "person", "person_key"),
    ("logged_time", "issue_key", "issue", "issue_key"),
    ("fixed_in", "issue_key", "issue", "issue_key"),
    ("fixed_in", "version_id", "fix_version", "version_id"),
    ("issue_link", "from_issue_key", "issue", "issue_key"),
    ("issue_link", "to_issue_key", "issue", "issue_key"),
)
# The names dbt gives a table while it swaps it in; one left behind means a
# build stopped partway.
LEFTOVER = ("__dbt_tmp", "__dbt_backup")
# An index definition without its name, which dbt regenerates on every build.
_INDEX_NAME = re.compile(r"^(CREATE (?:UNIQUE )?INDEX )\S+ (ON .*)$")

_RELATIONS_SQL = """
SELECT relname::text, relkind::text
FROM pg_class
WHERE relnamespace = 'model'::regnamespace AND relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY 1
"""

_FOREIGN_KEYS_SQL = """
SELECT
    tbl.relname::text,
    (SELECT string_agg(att.attname, ', ' ORDER BY key.ord)
     FROM unnest(con.conkey) WITH ORDINALITY AS key (attnum, ord)
     JOIN pg_attribute AS att ON att.attrelid = con.conrelid AND att.attnum = key.attnum),
    ref.relname::text,
    (SELECT string_agg(att.attname, ', ' ORDER BY key.ord)
     FROM unnest(con.confkey) WITH ORDINALITY AS key (attnum, ord)
     JOIN pg_attribute AS att ON att.attrelid = con.confrelid AND att.attnum = key.attnum)
FROM pg_constraint AS con
JOIN pg_class AS tbl ON tbl.oid = con.conrelid
JOIN pg_class AS ref ON ref.oid = con.confrelid
WHERE con.connamespace = 'model'::regnamespace AND con.contype = 'f'
ORDER BY 1, 2
"""

_INDEXES_SQL = "SELECT indexdef FROM pg_indexes WHERE schemaname = 'model'"


@dataclass(frozen=True)
class SqlState:
    """What `read_sql_state` saw in `model`.

    `relations` is each relation's name and `relkind`; `load_ids` the distinct
    `load_id` values of each expected table that exists; `foreign_keys` every
    declared foreign key; `indexes` every index definition without its name,
    sorted; `timezone` the reader session's zone.
    """

    relations: tuple[tuple[str, str], ...]
    load_ids: Mapping[str, tuple[str | None, ...]]
    foreign_keys: tuple[ForeignKey, ...]
    indexes: tuple[str, ...]
    timezone: str


def index_shape(definition: str) -> str:
    """An index definition from `pg_indexes` with its name taken out."""
    match = _INDEX_NAME.match(definition)
    return f"{match.group(1)}{match.group(2)}" if match else definition


def read_sql_state(conn: psycopg.Connection) -> SqlState:
    """Read the relations, load IDs, foreign keys, indexes and session zone of `model`."""
    relations = tuple((str(name), str(kind)) for name, kind in conn.execute(_RELATIONS_SQL))
    tables = {name for name, kind in relations if kind == "r"}
    load_ids = {
        table: tuple(
            row[0]
            for row in conn.execute(
                sql.SQL("SELECT DISTINCT load_id FROM {}").format(sql.Identifier(SCHEMA, table))
            )
        )
        for table in TABLES
        if table in tables
    }
    foreign_keys = tuple(
        (str(table), str(column), str(ref), str(ref_column))
        for table, column, ref, ref_column in conn.execute(_FOREIGN_KEYS_SQL)
    )
    indexes = tuple(sorted(index_shape(str(row[0])) for row in conn.execute(_INDEXES_SQL)))
    zone = conn.execute("SHOW timezone").fetchone()
    return SqlState(
        relations=relations,
        load_ids=load_ids,
        foreign_keys=foreign_keys,
        indexes=indexes,
        timezone=str(zone[0]) if zone else "",
    )


def _relation_problems(relations: tuple[tuple[str, str], ...]) -> list[str]:
    problems = []
    for name, kind in relations:
        if any(marker in name for marker in LEFTOVER):
            problems.append(f"{SCHEMA}.{name} is left over from a dbt build that stopped partway")
        elif name not in TABLES or kind != "r":
            problems.append(f"{SCHEMA}.{name} (relkind {kind}) is not one of the 7 tables")
    found = {name for name, kind in relations if kind == "r"}
    problems.extend(f"{SCHEMA}.{table} is missing" for table in TABLES if table not in found)
    return problems


def _load_id_problems(load_ids: Mapping[str, tuple[str | None, ...]], load_id: str) -> list[str]:
    problems = []
    for table, held in load_ids.items():
        if held != (load_id,):
            shown = ", ".join(sorted("null" if value is None else value for value in held))
            problems.append(
                f"{SCHEMA}.{table} holds load_id {shown or 'none (no rows)'},"
                f" expected only {load_id}"
            )
    return problems


def _foreign_key_problems(foreign_keys: tuple[ForeignKey, ...]) -> list[str]:
    def shown(key: ForeignKey) -> str:
        table, column, ref, ref_column = key
        return f"{table}.{column} to {ref}.{ref_column}"

    found = set(foreign_keys)
    expected = set(FOREIGN_KEYS)
    return [f"foreign key missing: {shown(key)}" for key in FOREIGN_KEYS if key not in found] + [
        f"foreign key not expected: {shown(key)}" for key in foreign_keys if key not in expected
    ]


def sql_state_problems(observed: SqlState, load_id: str) -> tuple[str, ...]:
    """Every way `model` differs from spec 0011 AC-4; empty means the check passes.

    Exactly the 7 tables and no leftover `__dbt_tmp` table, one `load_id` in
    each equal to `load_id`, the 10 foreign keys, and the session zone `UTC`.
    """
    problems = [
        *_relation_problems(observed.relations),
        *_load_id_problems(observed.load_ids, load_id),
        *_foreign_key_problems(observed.foreign_keys),
    ]
    if observed.timezone != TIMEZONE:
        problems.append(f"the reader session zone is {observed.timezone!r}, not {TIMEZONE}")
    return tuple(problems)
