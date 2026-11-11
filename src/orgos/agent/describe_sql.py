"""The schema description of the SQL store, rendered from the catalog and the meanings (spec 0012).

`render_sql_description` is pure. Its structure input is what the live catalog
holds for the 7 `model` tables, read as `orgos_reader` with keys from
`pg_constraint` and indexes from `pg_indexes` (AC-8); its words come from
`meanings.toml`, so the graph description says the same things in the same
sentences. Every list is sorted, so the same load gives the same text.
`read_sql_structure` and `read_sql_values` are the only functions here that
touch Postgres; they run on the reader connection through the one SQL
executor, and `tables_from_catalog` turns the catalog rows into tables, pure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from orgos.agent.errors import AgentError
from orgos.agent.meanings import (
    LINK_TYPE_KEY,
    SQL_BOOKKEEPING,
    VALUE_KEYS,
    Description,
    LinkWording,
    Meanings,
    ValueLists,
    general_section,
    meaning_text,
    values_section,
)
from orgos.sqlstore.query import run_sql

SCHEMA = "model"

# The catalog reads of AC-8: keys from `pg_constraint`, indexes from
# `pg_indexes`, never `information_schema.table_constraints` (spec 0007 AC-11).
CATALOG_COLUMNS = """
SELECT c.relname AS table_name, a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type, a.attnotnull AS not_null
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
WHERE c.relnamespace = 'model'::regnamespace AND c.relkind IN ('r', 'p')
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""
CATALOG_KEYS = """
SELECT c.relname AS table_name, con.conname AS name, con.contype AS kind,
  array(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, o)
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n
        ORDER BY k.o) AS columns,
  f.relname AS references_table,
  array(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, o)
        JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n
        ORDER BY k.o) AS references_columns
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
LEFT JOIN pg_class f ON f.oid = con.confrelid
WHERE c.relnamespace = 'model'::regnamespace AND con.contype IN ('p', 'f')
ORDER BY c.relname, con.conname
"""
CATALOG_INDEXES = (
    "SELECT tablename AS table_name, indexdef AS definition FROM pg_indexes"
    " WHERE schemaname = 'model' ORDER BY tablename, indexdef"
)
LINK_WORDINGS = (
    "SELECT DISTINCT link_type_name AS name, outward_label AS outward, inward_label AS inward"
    " FROM model.issue_link ORDER BY 1, 2, 3"
)
# The column list of a plain index definition: `... USING btree (a, b)`.
_INDEX_COLUMNS = re.compile(r" USING \w+ \(([^()]*)\)$")

# The meaning key of a column, where it is not `<table>.<column>`: the five
# folded links of an issue, and the two ends of the tables that are links.
COLUMN_KEYS: Mapping[tuple[str, str], str] = {
    ("issue", "project_key"): "in_project",
    ("issue", "assignee_key"): "assigned_to",
    ("issue", "reporter_key"): "reported_by",
    ("issue", "creator_key"): "created_by",
    ("issue", "parent_key"): "has_parent",
    ("fixed_in", "issue_key"): "fixed_in.start",
    ("fixed_in", "version_id"): "fixed_in.end",
    ("issue_link", "from_issue_key"): "issue_link.start",
    ("issue_link", "to_issue_key"): "issue_link.end",
    ("logged_time", "person_key"): "logged_time.start",
    ("logged_time", "issue_key"): "logged_time.end",
}


@dataclass(frozen=True)
class SqlColumn:
    """One column as the catalog holds it."""

    name: str
    data_type: str
    not_null: bool


@dataclass(frozen=True)
class ForeignKey:
    """One foreign key: its columns and the table and columns it references."""

    columns: tuple[str, ...]
    table: str
    references: tuple[str, ...]


@dataclass(frozen=True)
class SqlTable:
    """One `model` table: columns in catalog order, its primary key, foreign keys and indexes."""

    name: str
    columns: tuple[SqlColumn, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    indexes: tuple[tuple[str, ...], ...]


def index_columns(definition: str) -> tuple[str, ...]:
    """The columns of one `pg_indexes` definition; raises `AgentError` when it is not plain."""
    match = _INDEX_COLUMNS.search(definition)
    if match is None:
        raise AgentError(f"cannot read the columns of the index {definition!r}")
    return tuple(part.strip().strip('"') for part in match.group(1).split(","))


def tables_from_catalog(
    columns: tuple[Mapping[str, Any], ...],
    keys: tuple[Mapping[str, Any], ...],
    indexes: tuple[Mapping[str, Any], ...],
) -> tuple[SqlTable, ...]:
    """The `model` tables held in the three catalog reads, sorted by name."""
    tables = []
    for name in sorted({str(row["table_name"]) for row in columns}):
        mine = [row for row in keys if row["table_name"] == name]
        primary = next((tuple(row["columns"]) for row in mine if row["kind"] == "p"), ())
        tables.append(
            SqlTable(
                name=name,
                columns=tuple(
                    SqlColumn(str(row["column_name"]), str(row["data_type"]), bool(row["not_null"]))
                    for row in columns
                    if row["table_name"] == name
                ),
                primary_key=primary,
                foreign_keys=tuple(
                    ForeignKey(
                        tuple(row["columns"]),
                        str(row["references_table"]),
                        tuple(row["references_columns"]),
                    )
                    for row in mine
                    if row["kind"] == "f"
                ),
                indexes=tuple(
                    index_columns(str(row["definition"]))
                    for row in indexes
                    if row["table_name"] == name
                ),
            )
        )
    return tuple(tables)


def read_sql_structure(conn: psycopg.Connection, *, timeout_s: float) -> tuple[SqlTable, ...]:
    """Read the columns, keys and indexes of every `model` table from the catalog."""
    return tables_from_catalog(
        run_sql(conn, CATALOG_COLUMNS, timeout_s=timeout_s).rows,
        run_sql(conn, CATALOG_KEYS, timeout_s=timeout_s).rows,
        run_sql(conn, CATALOG_INDEXES, timeout_s=timeout_s).rows,
    )


def read_sql_values(conn: psycopg.Connection, *, timeout_s: float) -> ValueLists:
    """Read the distinct listed values of `model.issue` and every link type's wording."""
    values = {}
    for key in VALUE_KEYS:
        table, _, column = key.partition(".")
        statement = (
            f"SELECT DISTINCT {column} AS value FROM {SCHEMA}.{table}"
            f" WHERE {column} IS NOT NULL ORDER BY 1"
        )
        rows = run_sql(conn, statement, timeout_s=timeout_s).rows
        values[key] = tuple(str(row["value"]) for row in rows)
    links = tuple(
        LinkWording(str(row["name"]), row["outward"], row["inward"])
        for row in run_sql(conn, LINK_WORDINGS, timeout_s=timeout_s).rows
    )
    return ValueLists(values=values, link_types=links)


def column_key(table: str, column: str) -> str:
    """The meaning key of one column of a `model` table."""
    return COLUMN_KEYS.get((table, column), f"{table}.{column}")


def _column_line(table: SqlTable, column: SqlColumn, meanings: Meanings) -> str:
    parts = [f"{column.name} {column.data_type}"]
    if table.primary_key == (column.name,):
        parts.append("primary key")
    elif column.not_null:
        parts.append("not null")
    parts.extend(
        f"references {SCHEMA}.{key.table}({key.references[0]})"
        for key in sorted(table.foreign_keys, key=lambda key: (key.columns, key.table))
        if key.columns == (column.name,)
    )
    return f"- {', '.join(parts)}: {meaning_text(meanings, column_key(table.name, column.name))}"


def _table_lines(table: SqlTable, meanings: Meanings) -> tuple[list[str], set[str]]:
    lines = [f"{SCHEMA}.{table.name}: {meaning_text(meanings, table.name)}"]
    keys = {table.name}
    for column in table.columns:
        if column.name in SQL_BOOKKEEPING:
            continue
        lines.append(_column_line(table, column, meanings))
        keys.add(column_key(table.name, column.name))
    if len(table.primary_key) > 1:
        lines.append(f"Primary key: ({', '.join(table.primary_key)})")
    composite = sorted(
        (key for key in table.foreign_keys if len(key.columns) > 1),
        key=lambda key: (key.columns, key.table, key.references),
    )
    lines.extend(
        f"Foreign key: ({', '.join(key.columns)}) references"
        f" {SCHEMA}.{key.table}({', '.join(key.references)})"
        for key in composite
    )
    indexes = sorted(index for index in set(table.indexes) if index != table.primary_key)
    if indexes:
        lines.append("Indexes: " + ", ".join(f"({', '.join(index)})" for index in indexes))
    return lines, keys


def render_sql_description(
    tables: tuple[SqlTable, ...], meanings: Meanings, values: ValueLists
) -> Description:
    """The SQL schema description: the general sentences, each table, then the value lists."""
    lines = [f"Schema description: PostgreSQL, schema {SCHEMA}", "", *general_section(meanings)]
    lines.extend(("", "Tables"))
    keys: set[str] = set()
    for table in sorted(tables, key=lambda table: table.name):
        table_lines, table_keys = _table_lines(table, meanings)
        lines.extend(table_lines)
        keys |= table_keys
    names = {key: key for key in VALUE_KEYS} | {LINK_TYPE_KEY: LINK_TYPE_KEY}
    lines.extend(("", *values_section(values, names, lambda name: f'"{name}"')))
    return Description(text="\n".join(lines), keys=frozenset(keys))
