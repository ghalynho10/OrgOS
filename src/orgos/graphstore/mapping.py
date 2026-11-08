"""The mapping from the 12 `model_int` tables to the graph, read from `mapping.toml`.

Spec 0008 designs the graph and spec 0010 the file's layout. `load_mapping`
reads the file into frozen dataclasses and runs the checks a load needs before
it trusts the file (the table count, the structural columns, the 17 link
values). `coverage_problems` and `issue_pair_problems` are the contract checks
of spec 0008 AC-1, AC-5, AC-11 and AC-15 against the columns the dbt YAML
declares; the unit tests run them, so they need no database. The loader holds
no label, type or property name of its own: they all come from here.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orgos.graphstore import GraphStoreError

MAPPING_FILE = Path(__file__).with_name("mapping.toml")

TABLE_COUNT = 12
LINK_TYPE_COUNT = 17
# A label, relationship type or property name must match this before it goes
# into a statement, because none of them can be a Cypher parameter.
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PROPERTY_TYPES = frozenset({"String", "Boolean", "Integer", "datetime"})
# The dbt `data_type` of a column and the property type it becomes (spec 0008 AC-15).
COLUMN_TYPES: Mapping[str, str] = {
    "text": "String",
    "boolean": "Boolean",
    "bigint": "Integer",
    "timestamptz": "datetime",
}

# The structural columns of an edge table: the two ends and the type. `edge_id`
# becomes `edgeId` on every relationship through this shared rule, and
# `link_type_name` of `issue_link` equals `edge_type` (spec 0006 AC-11).
EDGE_ID = "edge_id"
EDGE_ID_PROPERTY = "edgeId"
SRC_ID = "src_id"
DST_ID = "dst_id"
TYPE_COLUMN = "edge_type"
LINK_TYPE_NAME = "link_type_name"
STRUCTURAL_COLUMNS = frozenset({EDGE_ID, SRC_ID, DST_ID, TYPE_COLUMN, LINK_TYPE_NAME})
# Every table carries the load ID it was built from (spec 0008 AC-13).
LOAD_ID = "load_id"


@dataclass(frozen=True)
class Column:
    """One column of a `model_int` table and the property it becomes."""

    column: str
    property: str
    type: str


@dataclass(frozen=True)
class NodeMap:
    """A table that becomes one node label, merged by its key property."""

    table: str
    label: str
    key: Column
    constraint: str
    columns: tuple[Column, ...]

    @property
    def read_columns(self) -> tuple[str, ...]:
        """The columns the load reads, key first."""
        return (self.key.column, *(column.column for column in self.columns))


@dataclass(frozen=True)
class End:
    """One end of a relationship: the label and its key property."""

    label: str
    key: str


@dataclass(frozen=True)
class RelationshipMap:
    """A table that becomes relationships, of one type or split by its type column."""

    table: str
    start: End
    end: End
    columns: tuple[Column, ...]
    type: str | None = None
    type_column: str | None = None
    types: tuple[tuple[str, str], ...] = ()

    @property
    def relationship_types(self) -> tuple[str, ...]:
        """Every type this table can produce."""
        return (self.type,) if self.type is not None else tuple(kind for _, kind in self.types)

    @property
    def read_columns(self) -> tuple[str, ...]:
        """The columns the load reads: `edge_id`, the two ends, the type column, the properties."""
        type_columns = (self.type_column,) if self.type_column is not None else ()
        return (
            EDGE_ID,
            SRC_ID,
            DST_ID,
            *type_columns,
            *(column.column for column in self.columns),
        )


@dataclass(frozen=True)
class GraphMapping:
    """The whole mapping: four node tables, eight relationship tables, the exclusions."""

    nodes: tuple[NodeMap, ...]
    relationships: tuple[RelationshipMap, ...]
    excluded: tuple[str, ...]

    @property
    def tables(self) -> tuple[str, ...]:
        """Every mapped table, nodes first, in file order."""
        return tuple(node.table for node in self.nodes) + tuple(
            relationship.table for relationship in self.relationships
        )

    @property
    def relationship_types(self) -> tuple[str, ...]:
        """All 24 relationship types, in file order."""
        return tuple(
            kind for relationship in self.relationships for kind in relationship.relationship_types
        )

    def node(self, label: str) -> NodeMap:
        """The node table of `label`."""
        return next(node for node in self.nodes if node.label == label)


def _column(raw: Mapping[str, Any]) -> Column:
    return Column(column=str(raw["column"]), property=str(raw["property"]), type=str(raw["type"]))


def _end(raw: Mapping[str, Any]) -> End:
    return End(label=str(raw["label"]), key=str(raw["key"]))


def parse_mapping(data: Mapping[str, Any]) -> GraphMapping:
    """The mapping held in parsed TOML; raises `GraphStoreError` naming every problem."""
    try:
        mapping = GraphMapping(
            nodes=tuple(
                NodeMap(
                    table=str(node["table"]),
                    label=str(node["label"]),
                    key=_column(node["key"]),
                    constraint=str(node["key"]["constraint"]),
                    columns=tuple(_column(column) for column in node["columns"]),
                )
                for node in data["node"]
            ),
            relationships=tuple(
                RelationshipMap(
                    table=str(relationship["table"]),
                    start=_end(relationship["start"]),
                    end=_end(relationship["end"]),
                    columns=tuple(_column(column) for column in relationship["columns"]),
                    type=relationship.get("type"),
                    type_column=relationship.get("type_column"),
                    types=tuple(
                        (str(value), str(kind))
                        for value, kind in relationship.get("types", {}).items()
                    ),
                )
                for relationship in data["relationship"]
            ),
            excluded=tuple(str(table) for table in data["excluded"]),
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise GraphStoreError(f"The graph mapping is malformed: missing or bad {error}") from None
    problems = mapping_problems(mapping)
    if problems:
        raise GraphStoreError("The graph mapping fails its checks:\n  " + "\n  ".join(problems))
    return mapping


def load_mapping(path: Path = MAPPING_FILE) -> GraphMapping:
    """Read and check the mapping file; raises `GraphStoreError` when it cannot be used."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise GraphStoreError(f"Cannot read the graph mapping {path}: {error.strerror}") from None
    except tomllib.TOMLDecodeError as error:
        raise GraphStoreError(f"The graph mapping {path} is not valid TOML: {error}") from None
    return parse_mapping(data)


def link_type(value: str) -> str:
    """The relationship type of a link type name: capitals, a space as `_` (spec 0008 AC-7)."""
    return value.upper().replace(" ", "_")


def _name_problems(mapping: GraphMapping) -> list[str]:
    names = [
        *(node.label for node in mapping.nodes),
        *mapping.relationship_types,
        *(node.key.property for node in mapping.nodes),
        *(column.property for node in mapping.nodes for column in node.columns),
        *(column.property for rel in mapping.relationships for column in rel.columns),
    ]
    return [
        f"{name!r} is not a plain identifier" for name in names if not IDENTIFIER.fullmatch(name)
    ]


def _relationship_problems(mapping: GraphMapping, relationship: RelationshipMap) -> list[str]:
    table = relationship.table
    problems = []
    keys = {node.label: node.key.property for node in mapping.nodes}
    for side, end in (("start", relationship.start), ("end", relationship.end)):
        if keys.get(end.label) != end.key:
            problems.append(f"{table} {side} {end.label}.{end.key} is not a mapped node key")
    if relationship.type_column is None:
        if relationship.type is None or relationship.types:
            problems.append(f"{table} needs exactly one fixed type, or a type column with types")
        return problems
    if relationship.type is not None or relationship.type_column != TYPE_COLUMN:
        problems.append(f"{table} splits by {relationship.type_column!r}, expected {TYPE_COLUMN!r}")
    if len(relationship.types) != LINK_TYPE_COUNT:
        problems.append(f"{table} lists {len(relationship.types)} link types, expected 17")
    problems.extend(
        f"{table} maps {value!r} to {kind}, expected {link_type(value)}"
        for value, kind in relationship.types
        if kind != link_type(value)
    )
    return problems


def mapping_problems(mapping: GraphMapping) -> tuple[str, ...]:
    """The checks a load runs on the file before it trusts it, as problems.

    Exactly 12 distinct tables, the structural columns never stored as
    properties, known property types, plain identifiers, ends that are mapped
    node keys, and the 17 link values each mapped to its type.
    """
    problems = []
    tables = [*mapping.tables, *mapping.excluded]
    if len(tables) != TABLE_COUNT or len(set(tables)) != len(tables):
        problems.append(f"names {len(tables)} tables ({', '.join(tables)}), expected 12 distinct")
    columns = [
        (node.table, column) for node in mapping.nodes for column in (node.key, *node.columns)
    ] + [(rel.table, column) for rel in mapping.relationships for column in rel.columns]
    for table, column in columns:
        if column.column in STRUCTURAL_COLUMNS:
            problems.append(f"{table}.{column.column} is structural and cannot be a property")
        if column.type not in PROPERTY_TYPES:
            problems.append(f"{table}.{column.column} has unknown type {column.type!r}")
    problems.extend(_name_problems(mapping))
    for relationship in mapping.relationships:
        problems.extend(_relationship_problems(mapping, relationship))
    return tuple(problems)


def coverage_problems(
    mapping: GraphMapping, declared: Mapping[str, Mapping[str, str]]
) -> tuple[str, ...]:
    """Each table or column the mapping and the dbt YAML disagree on (spec 0008 AC-1, AC-11, AC-15).

    `declared` holds each `model_int` table's columns and their `data_type`.
    A table must be mapped or excluded and every mapped table declared. Every
    column must be a property whose type follows `COLUMN_TYPES`, or structural;
    every property must be a declared column, so nothing is derived.
    """
    problems: list[str] = []
    mapped = set(mapping.tables) | set(mapping.excluded)
    problems.extend(
        f"{table} is neither mapped nor excluded" for table in declared if table not in mapped
    )
    problems.extend(
        f"{table} is mapped but not a model_int table" for table in mapped if table not in declared
    )
    properties: dict[str, tuple[Column, ...]] = {
        node.table: (node.key, *node.columns) for node in mapping.nodes
    } | {rel.table: rel.columns for rel in mapping.relationships}
    structural: dict[str, frozenset[str]] = {node.table: frozenset() for node in mapping.nodes} | {
        rel.table: frozenset({EDGE_ID, SRC_ID, DST_ID, TYPE_COLUMN})
        | (frozenset({LINK_TYPE_NAME}) if rel.type_column else frozenset())
        for rel in mapping.relationships
    }
    for table, columns in properties.items():
        types = declared.get(table)
        if types is None:
            continue
        by_column = {column.column: column for column in columns}
        for name, data_type in types.items():
            column = by_column.get(name)
            if column is None:
                if name not in structural[table]:
                    problems.append(f"{table}.{name} is neither a property nor structural")
            elif COLUMN_TYPES.get(data_type) != column.type:
                problems.append(
                    f"{table}.{name} is {data_type} but mapped as {column.type}"
                    if data_type in COLUMN_TYPES
                    else f"{table}.{name} has type {data_type}, which no property type allows"
                )
        problems.extend(
            f"{table}.{name} is mapped but not a declared column"
            for name in by_column
            if name not in types
        )
        problems.extend(
            f"{table}.{name} is structural but not a declared column"
            for name in sorted(structural[table])
            if name not in types
        )
    return tuple(problems)


def issue_pair_problems(mapping: GraphMapping) -> tuple[str, ...]:
    """Each relationship type between two `Issue` nodes other than `HAS_PARENT` and the 17 links.

    Spec 0008 AC-5: spec 0006 AC-25 relies on these being the only ones.
    """
    links = next((rel.relationship_types for rel in mapping.relationships if rel.type_column), ())
    allowed = {"HAS_PARENT", *links}
    return tuple(
        f"{kind} runs between two Issue nodes"
        for rel in mapping.relationships
        if rel.start.label == "Issue" and rel.end.label == "Issue"
        for kind in rel.relationship_types
        if kind not in allowed
    )
