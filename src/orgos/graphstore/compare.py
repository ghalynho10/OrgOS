"""The value level compare of the graph with `model_int` (spec 0008 AC-28, spec 0010 AC-18).

Every node is read back with all its properties and every relationship with
its properties, its type and the keys of its two ends, then compared in Python
with the `model_int` row after the load's own conversion, in both directions.
No Cypher derives anything. `read_nodes` and `read_relationships` are the only
functions that touch Neo4j; the comparison itself is pure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from neo4j import Driver

from orgos.graphstore import DATABASE, SHOWN
from orgos.graphstore.convert import node_row, relationship_row, type_of
from orgos.graphstore.mapping import EDGE_ID_PROPERTY, NodeMap, RelationshipMap
from orgos.graphstore.statements import node_properties, relationship_properties

# One difference: its kind (one of the names below) and what it names.
Difference = tuple[str, str]
MISSING_NODE = "missing node"
EXTRA_NODE = "extra node"
NODE_PROPERTY = "node property"
MISSING_RELATIONSHIP = "missing relationship"
EXTRA_RELATIONSHIP = "extra relationship"
REPEATED = "repeated key"
RELATIONSHIP_TYPE = "relationship type"
RELATIONSHIP_ENDS = "relationship ends"
RELATIONSHIP_PROPERTY = "relationship property"


@dataclass(frozen=True)
class Differences:
    """One kind of difference: how many there are and the first 20 named."""

    count: int
    shown: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    """The compare's result: how many nodes and relationships it read back, and each kind found."""

    nodes: int
    relationships: int
    differences: Mapping[str, Differences]

    @property
    def problems(self) -> tuple[str, ...]:
        """One line per kind of difference, naming up to 20."""
        lines = []
        for kind, found in self.differences.items():
            more = found.count - len(found.shown)
            tail = f" and {more:,} more" if more > 0 else ""
            lines.append(f"{kind}: {found.count:,} ({', '.join(found.shown)}{tail})")
        return tuple(lines)

    def record(self) -> dict[str, Any]:
        """The run record object for this check."""
        return {
            "nodes_compared": self.nodes,
            "relationships_compared": self.relationships,
            "differences": {
                kind: {"count": found.count, "shown": list(found.shown)}
                for kind, found in self.differences.items()
            },
        }


def native(value: object) -> object:
    """A value read from Neo4j as the Python value psycopg gives (a driver `DateTime` included)."""
    to_native = getattr(value, "to_native", None)
    return to_native() if callable(to_native) else value


def same(expected: object, found: object) -> bool:
    """Equal and of the same Python type, so `True` never passes for `1`."""
    found = native(found)
    return type(expected) is type(found) and expected == found


def property_differences(
    expected: Mapping[str, object], found: Mapping[str, object]
) -> tuple[str, ...]:
    """The names of the properties that differ, a property absent on one side included."""
    return tuple(
        sorted(
            name
            for name in expected.keys() | found.keys()
            if name not in expected or name not in found or not same(expected[name], found[name])
        )
    )


def _keyed(
    found: Iterable[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], object], where: str
) -> tuple[dict[object, Mapping[str, Any]], list[Difference]]:
    # The items read back by their key; a second item with the same key is a difference.
    seen: dict[object, Mapping[str, Any]] = {}
    repeated = []
    for item in found:
        value = key(item)
        if value in seen:
            repeated.append((REPEATED, f"{where} {value}"))
        else:
            seen[value] = item
    return seen, repeated


def compare_nodes(
    node: NodeMap, rows: Iterable[Mapping[str, Any]], found: Iterable[Mapping[str, Any]]
) -> list[Difference]:
    """Each difference between one node table and the nodes of its label read back."""
    expected = {}
    for row in rows:
        converted = node_row(node, row)
        expected[converted["key"]] = {node.key.property: converted["key"], **converted["props"]}
    seen, differences = _keyed(found, lambda props: props.get(node.key.property), node.label)
    for key, props in expected.items():
        held = seen.get(key)
        if held is None:
            differences.append((MISSING_NODE, f"{node.label} {key}"))
            continue
        differences.extend(
            (NODE_PROPERTY, f"{node.label} {key} {name}")
            for name in property_differences(props, held)
        )
    differences.extend(
        (EXTRA_NODE, f"{node.label} {key}")
        for key in sorted(map(str, seen.keys() - expected.keys()))
    )
    return differences


def compare_relationships(
    relationship: RelationshipMap,
    rows: Iterable[Mapping[str, Any]],
    found: Iterable[Mapping[str, Any]],
) -> list[Difference]:
    """Each difference between one edge table and its relationships read back.

    `found` holds, per relationship, its `type`, the `src` and `dst` end keys
    and its `props`. Properties, the type and both ends are compared, because a
    reversed relationship has the right properties.
    """
    table = relationship.table
    expected = {}
    for row in rows:
        converted = relationship_row(relationship, row)
        expected[converted["edgeId"]] = (
            type_of(relationship, row),
            (converted["src"], converted["dst"]),
            {EDGE_ID_PROPERTY: converted["edgeId"], **converted["props"]},
        )
    seen, differences = _keyed(found, lambda item: item["props"].get(EDGE_ID_PROPERTY), table)
    for edge_id, (kind, ends, props) in expected.items():
        where = f"{table} {edge_id}"
        held = seen.get(edge_id)
        if held is None:
            differences.append((MISSING_RELATIONSHIP, where))
            continue
        if held["type"] != kind:
            differences.append((RELATIONSHIP_TYPE, f"{where}: {kind} expected, {held['type']}"))
        if (held["src"], held["dst"]) != ends:
            differences.append(
                (
                    RELATIONSHIP_ENDS,
                    f"{where}: {ends[0]}->{ends[1]} expected, {held['src']}->{held['dst']}",
                )
            )
        differences.extend(
            (RELATIONSHIP_PROPERTY, f"{where} {name}")
            for name in property_differences(props, held["props"])
        )
    differences.extend(
        (EXTRA_RELATIONSHIP, f"{table} {edge_id}")
        for edge_id in sorted(map(str, seen.keys() - expected.keys()))
    )
    return differences


def comparison(nodes: int, relationships: int, differences: Sequence[Difference]) -> Comparison:
    """The `Comparison` of everything compared: each kind counted, the first 20 of it named."""
    by_kind: dict[str, list[str]] = {}
    for kind, text in differences:
        by_kind.setdefault(kind, []).append(text)
    return Comparison(
        nodes=nodes,
        relationships=relationships,
        differences={
            kind: Differences(count=len(texts), shown=tuple(texts[:SHOWN]))
            for kind, texts in by_kind.items()
        },
    )


def read_nodes(driver: Driver, node: NodeMap) -> list[dict[str, Any]]:
    """Every node of one label, as its property map."""
    records = driver.execute_query(node_properties(node), database_=DATABASE).records
    return [dict(record["props"]) for record in records]


def read_relationships(driver: Driver, relationship: RelationshipMap) -> list[dict[str, Any]]:
    """Every relationship of each type the table produces, with its type and end keys."""
    found: list[dict[str, Any]] = []
    for kind in relationship.relationship_types:
        statement = relationship_properties(kind, relationship.start, relationship.end)
        records = driver.execute_query(statement, database_=DATABASE).records
        found.extend(
            {"type": kind, "src": record["src"], "dst": record["dst"], "props": record["props"]}
            for record in records
        )
    return found
