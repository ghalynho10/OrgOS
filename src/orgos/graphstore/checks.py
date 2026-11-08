"""What "the graph is in the expected state" means, written once.

Spec 0010 AC-10, AC-15, AC-17 and AC-20. The load calls these, and features
12 and 13 call the same ones in the shared preflight. `read_graph_state` and
`read_counts` are the only functions that touch Neo4j; the others are pure and
judge what they returned. Expected constraints, indexes, labels and types all
come from the mapping, so no function here holds a name of its own.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from neo4j import Driver

from orgos.graphstore import DATABASE, GraphStoreError
from orgos.graphstore.mapping import GraphMapping
from orgos.graphstore.statements import (
    COUNT_BY_TYPE,
    COUNT_NODES,
    COUNT_RELATIONSHIPS,
    PREFIX,
    SHOW_CONSTRAINTS,
    SHOW_DATABASE,
    SHOW_INDEXES,
    count_label,
)

UNIQUENESS = "NODE_PROPERTY_UNIQUENESS"

# (type, entityType, labelsOrTypes, properties, owned by a constraint): what
# the schema checks compare, never the names Neo4j generates.
Signature = tuple[str, str, tuple[str, ...], tuple[str, ...], bool]


@dataclass(frozen=True)
class GraphState:
    """What `read_graph_state` saw: the schema, the distinct load IDs and the access of `neo4j`."""

    constraints: tuple[Mapping[str, Any], ...]
    indexes: tuple[Mapping[str, Any], ...]
    node_load_ids: tuple[str | None, ...]
    relationship_load_ids: tuple[str | None, ...]
    access: str | None


def read_graph_state(driver: Driver) -> GraphState:
    """Read `SHOW CONSTRAINTS`, `SHOW INDEXES`, the distinct `loadId`s and `SHOW DATABASES`."""

    def read(query: str, database: str = DATABASE, **parameters: Any) -> list[dict[str, Any]]:
        records = driver.execute_query(query, parameters, database_=database).records
        return [record.data() for record in records]

    databases = read(SHOW_DATABASE, "system", name=DATABASE)
    return GraphState(
        constraints=tuple(read(SHOW_CONSTRAINTS)),
        indexes=tuple(read(SHOW_INDEXES)),
        node_load_ids=tuple(
            row["loadId"] for row in read(f"{PREFIX} MATCH (n) RETURN DISTINCT n.loadId AS loadId")
        ),
        relationship_load_ids=tuple(
            row["loadId"]
            for row in read(f"{PREFIX} MATCH ()-[r]->() RETURN DISTINCT r.loadId AS loadId")
        ),
        access=str(databases[0]["access"]) if databases else None,
    )


def _signature(item: Mapping[str, Any]) -> Signature:
    return (
        str(item["type"]),
        str(item["entityType"]),
        tuple(item.get("labelsOrTypes") or ()),
        tuple(item.get("properties") or ()),
        bool(item.get("owningConstraint")),
    )


def _describe(signature: Signature) -> str:
    kind, entity, labels, properties, owned = signature
    target = f"{entity} {','.join(labels)}({','.join(properties)})" if labels else entity
    return f"{kind} on {target}" + (" owned by a constraint" if owned else "")


def expected_constraints(mapping: GraphMapping) -> tuple[Signature, ...]:
    """The four uniqueness constraints of spec 0008 AC-19, one per label key."""
    return tuple(
        (UNIQUENESS, "NODE", (node.label,), (node.key.property,), False) for node in mapping.nodes
    )


def expected_indexes(mapping: GraphMapping) -> tuple[Signature, ...]:
    """The four range indexes those constraints own, and the two default lookup indexes."""
    return (
        *(("RANGE", "NODE", (node.label,), (node.key.property,), True) for node in mapping.nodes),
        ("LOOKUP", "NODE", (), (), False),
        ("LOOKUP", "RELATIONSHIP", (), (), False),
    )


def _differences(
    kind: str, expected: Iterable[Signature], found: Iterable[Mapping[str, Any]]
) -> list[str]:
    found_items = list(found)
    seen = Counter(_signature(item) for item in found_items)
    wanted = Counter(expected)
    problems = [
        f"missing {kind}: {_describe(signature)}" for signature in (wanted - seen).elements()
    ]
    unexpected = seen - wanted
    for item in found_items:
        signature = _signature(item)
        if unexpected[signature] > 0:
            unexpected[signature] -= 1
            problems.append(f"unexpected {kind} {item.get('name')}: {_describe(signature)}")
    return problems


def graph_schema_problems(observed: GraphState, mapping: GraphMapping) -> tuple[str, ...]:
    """Each missing or unexpected constraint or index, compared on what it is, not its name."""
    return (
        *_differences("constraint", expected_constraints(mapping), observed.constraints),
        *_differences("index", expected_indexes(mapping), observed.indexes),
    )


def check_run_records(
    sql: tuple[str, Mapping[str, Any]], graph: tuple[str, Mapping[str, Any]] | None
) -> tuple[str, ...]:
    """Problems with the run records, each naming its file; empty when they agree and say `ok`.

    The SQL record must say `status: ok` with a `dlt_load_id`. A graph record,
    when given, must also say `ok` and hold the same `dlt_load_id`.
    """
    problems = []
    sql_name, sql_record = sql
    load_id = sql_record.get("dlt_load_id")
    for name, record in (sql, *((graph,) if graph is not None else ())):
        if record.get("status") != "ok":
            failed = record.get("failed_step")
            at = f" (failed step {failed})" if failed else ""
            problems.append(f"{name} says status {record.get('status')!r}, not 'ok'{at}")
    if not load_id:
        problems.append(f"{sql_name} holds no dlt_load_id")
    if graph is not None:
        graph_name, graph_record = graph
        if graph_record.get("dlt_load_id") != load_id:
            problems.append(
                f"{graph_name} holds dlt_load_id {graph_record.get('dlt_load_id')},"
                f" but {sql_name} holds {load_id}"
            )
    return tuple(problems)


@dataclass(frozen=True)
class Count:
    """One label or relationship type: the `model_int` rows, `count`, and `count(DISTINCT key)`."""

    expected: int
    found: int
    distinct: int

    @property
    def ok(self) -> bool:
        """True when all three agree."""
        return self.expected == self.found == self.distinct

    def record(self) -> dict[str, int]:
        """The run record object for this count."""
        return {"expected": self.expected, "found": self.found, "distinct": self.distinct}


@dataclass(frozen=True)
class FoundCounts:
    """What `read_counts` saw: per label and per type `(count, distinct)`, and the two totals."""

    labels: Mapping[str, tuple[int, int]]
    types: Mapping[str, tuple[int, int]]
    nodes: int
    relationships: int


@dataclass(frozen=True)
class Reconciliation:
    """The per label and per type counts of spec 0008 AC-21, and every problem found."""

    labels: Mapping[str, Count]
    types: Mapping[str, Count]
    nodes: int
    relationships: int
    problems: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        """The run record object for this check."""
        return {
            "labels": {label: count.record() for label, count in self.labels.items()},
            "relationship_types": {kind: count.record() for kind, count in self.types.items()},
            "total_nodes": self.nodes,
            "total_relationships": self.relationships,
            "problems": list(self.problems),
        }


def read_counts(driver: Driver, mapping: GraphMapping) -> FoundCounts:
    """Count each label by its key, every relationship type by `edgeId`, and both totals."""

    def rows(query: str) -> list[dict[str, Any]]:
        return [record.data() for record in driver.execute_query(query, database_=DATABASE).records]

    labels = {}
    for node in mapping.nodes:
        row = rows(count_label(node))[0]
        labels[node.label] = (int(row["count"]), int(row["distinct"]))
    types = {
        str(row["type"]): (int(row["count"]), int(row["distinct"])) for row in rows(COUNT_BY_TYPE)
    }
    return FoundCounts(
        labels=labels,
        types=types,
        nodes=int(rows(COUNT_NODES)[0]["count"]),
        relationships=int(rows(COUNT_RELATIONSHIPS)[0]["count"]),
    )


def expected_counts(
    mapping: GraphMapping, table_rows: Mapping[str, int], type_rows: Mapping[str, int]
) -> dict[str, int]:
    """The expected count of every label and of all 24 types, from the live `model_int` counts.

    `table_rows` holds each table's row count and `type_rows` the `issue_link`
    rows per `edge_type` value; a link type with no rows is expected at 0.
    """
    expected = {node.label: table_rows[node.table] for node in mapping.nodes}
    for relationship in mapping.relationships:
        if relationship.type is not None:
            expected[relationship.type] = table_rows[relationship.table]
            continue
        types = dict(relationship.types)
        unknown = sorted(value for value in type_rows if value not in types)
        if unknown:
            raise GraphStoreError(
                f"{relationship.table} holds {relationship.type_column} values outside the"
                f" mapping: {', '.join(unknown)}"
            )
        for value, kind in relationship.types:
            expected[kind] = type_rows.get(value, 0)
    return expected


def reconcile(
    mapping: GraphMapping, expected: Mapping[str, int], found: FoundCounts
) -> Reconciliation:
    """Spec 0008 AC-21 and spec 0010 AC-15: every count three ways, and the totals add up.

    Per label `count(n) = count(DISTINCT key) = rows`; per type
    `count(r) = count(DISTINCT edgeId) = rows`; the total node count is the sum
    of the four labels and the total relationship count the sum of the 24
    types, so no other label or type can hide.
    """
    labels = {
        node.label: Count(expected[node.label], *found.labels.get(node.label, (0, 0)))
        for node in mapping.nodes
    }
    types = {
        kind: Count(expected[kind], *found.types.get(kind, (0, 0)))
        for kind in mapping.relationship_types
    }
    problems = [
        f"{name}: {count.expected:,} model_int rows, {count.found:,} found,"
        f" {count.distinct:,} distinct"
        for name, count in (*labels.items(), *types.items())
        if not count.ok
    ]
    problems.extend(
        f"relationship type {kind} is not in the mapping ({counts[0]:,} found)"
        for kind, counts in found.types.items()
        if kind not in types
    )
    label_sum = sum(count.found for count in labels.values())
    if found.nodes != label_sum:
        problems.append(
            f"{found.nodes:,} nodes in all, but the four labels add up to {label_sum:,}"
        )
    type_sum = sum(count.found for count in types.values())
    if found.relationships != type_sum:
        problems.append(
            f"{found.relationships:,} relationships in all, but the 24 types add up to {type_sum:,}"
        )
    return Reconciliation(
        labels=labels,
        types=types,
        nodes=found.nodes,
        relationships=found.relationships,
        problems=tuple(problems),
    )
