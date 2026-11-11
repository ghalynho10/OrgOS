"""The schema description of the SQL store, rendered from the catalog and the meanings (spec 0012).

`render_sql_description` is pure. Its structure input is what the live catalog
holds for the 7 `model` tables, read as `orgos_reader` with keys from
`pg_constraint` and indexes from `pg_indexes` (AC-8); its words come from
`meanings.toml`, so the graph description says the same things in the same
sentences. Every list is sorted, so the same load gives the same text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from orgos.agent.meanings import (
    LINK_TYPE_KEY,
    SQL_BOOKKEEPING,
    VALUE_KEYS,
    Description,
    Meanings,
    ValueLists,
    general_section,
    meaning_text,
    values_section,
)

SCHEMA = "model"

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
