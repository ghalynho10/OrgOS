"""Turn `model_int` rows into the parameters of the graph writes (spec 0010 AC-11 and AC-13).

Pure functions. A null becomes an absent property; an empty string, a
`datetime` with no zone, or a value of the wrong Python type fails the load,
naming the table and the row's key. Every `datetime` is converted with
`astimezone(UTC)` so Neo4j stores it in the `Z` form (spec 0008 AC-17).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from orgos.graphstore import GraphStoreError
from orgos.graphstore.mapping import (
    DST_ID,
    EDGE_ID,
    SRC_ID,
    Column,
    NodeMap,
    RelationshipMap,
)

# The Python type each property type must arrive as from psycopg.
PYTHON_TYPES: Mapping[str, type] = {
    "String": str,
    "Boolean": bool,
    "Integer": int,
    "datetime": datetime,
}


def convert_value(value: object, column: Column, table: str, key: str) -> object | None:
    """The value to store for `column`, or None for an absent property.

    Raises `GraphStoreError` for an empty string, a `datetime` with no zone,
    or a value whose type does not match the declared property type.
    """
    if value is None:
        return None
    where = f"{table} row {key}: {column.column}"
    expected = PYTHON_TYPES[column.type]
    # bool is a subclass of int, so an Integer must not accept True.
    if not isinstance(value, expected) or (column.type == "Integer" and isinstance(value, bool)):
        raise GraphStoreError(
            f"{where} is a {type(value).__name__}, but the mapping declares {column.type}"
        )
    if isinstance(value, str) and value == "":
        raise GraphStoreError(f"{where} is an empty string; model_int stores a null instead")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise GraphStoreError(f"{where} is a datetime with no time zone")
        return value.astimezone(UTC)
    return value


def properties(
    row: Mapping[str, Any], columns: Iterable[Column], table: str, key: str
) -> dict[str, object]:
    """The property map of one row: every non null column under its property name."""
    converted = {
        column.property: convert_value(row[column.column], column, table, key) for column in columns
    }
    return {name: value for name, value in converted.items() if value is not None}


def _key(value: object, table: str, column: str) -> str:
    if not isinstance(value, str) or value == "":
        raise GraphStoreError(f"{table} has a row whose {column} is {value!r}, not a key")
    return value


def node_row(node: NodeMap, row: Mapping[str, Any]) -> dict[str, object]:
    """The `$rows` entry of one node: its key and its properties."""
    key = _key(row[node.key.column], node.table, node.key.column)
    return {"key": key, "props": properties(row, node.columns, node.table, key)}


def relationship_row(relationship: RelationshipMap, row: Mapping[str, Any]) -> dict[str, object]:
    """The `$rows` entry of one relationship: both end keys, `edgeId` and its properties.

    A null end fails the load naming the table and `edge_id` (spec 0008 AC-16).
    """
    table = relationship.table
    edge_id = _key(row[EDGE_ID], table, EDGE_ID)
    ends = {}
    for name, column in (("src", SRC_ID), ("dst", DST_ID)):
        value = row[column]
        if not isinstance(value, str) or value == "":
            raise GraphStoreError(f"{table} row {edge_id}: {column} is {value!r}, not a key")
        ends[name] = value
    return {
        **ends,
        "edgeId": edge_id,
        "props": properties(row, relationship.columns, table, edge_id),
    }


def type_of(relationship: RelationshipMap, row: Mapping[str, Any]) -> str:
    """The relationship type of one row; an unlisted `edge_type` fails the load (spec 0008 AC-7)."""
    if relationship.type is not None:
        return relationship.type
    value = row[str(relationship.type_column)]
    kind = dict(relationship.types).get(value)
    if kind is None:
        raise GraphStoreError(
            f"{relationship.table} row {row[EDGE_ID]}: {relationship.type_column} {value!r}"
            " is not one of the 17 link types in the mapping"
        )
    return kind
