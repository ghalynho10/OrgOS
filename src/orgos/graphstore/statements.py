"""Build the Cypher of the graph load from the mapping (spec 0010 AC-9 to AC-11, AC-15).

Pure functions. A label, relationship type or property name cannot be a Cypher
parameter, so each is checked against a plain identifier pattern before it is
written into a statement; every value travels as a parameter. Every statement
starts with `CYPHER 25`.
"""

from __future__ import annotations

from orgos.graphstore import WIPE_BATCH_ROWS, GraphStoreError
from orgos.graphstore.mapping import EDGE_ID_PROPERTY, IDENTIFIER, End, NodeMap

PREFIX = "CYPHER 25"


def name(value: str) -> str:
    """`value` unchanged when it is a plain identifier; raises `GraphStoreError` otherwise."""
    if not IDENTIFIER.fullmatch(value):
        raise GraphStoreError(f"{value!r} is not a plain identifier and cannot go into Cypher")
    return value


def node_merge(node: NodeMap) -> str:
    """Merge a batch of `$rows` of one label by its key, then set its properties."""
    return (
        f"{PREFIX} UNWIND $rows AS row "
        f"MERGE (n:{name(node.label)} {{{name(node.key.property)}: row.key}}) "
        "SET n += row.props"
    )


def relationship_merge(kind: str, start: End, end: End) -> str:
    """Merge a batch of `$rows` of one relationship type by `edgeId`, both ends matched by key."""
    return (
        f"{PREFIX} UNWIND $rows AS row "
        f"MATCH (a:{name(start.label)} {{{name(start.key)}: row.src}}) "
        f"MATCH (b:{name(end.label)} {{{name(end.key)}: row.dst}}) "
        f"MERGE (a)-[r:{name(kind)} {{{EDGE_ID_PROPERTY}: row.edgeId}}]->(b) "
        "SET r += row.props"
    )


def existing_keys(end: End) -> str:
    """Which of `$keys` exist as nodes of one end, to name the rows that matched none (AC-12)."""
    key = name(end.key)
    return (
        f"{PREFIX} UNWIND $keys AS key "
        f"MATCH (n:{name(end.label)} {{{key}: key}}) RETURN n.{key} AS key"
    )


def wipe_relationships() -> str:
    """Delete every relationship, 10,000 per inner transaction (AC-9)."""
    return (
        f"{PREFIX} MATCH ()-[r]->() CALL (r) {{ DELETE r }} "
        f"IN TRANSACTIONS OF {WIPE_BATCH_ROWS} ROWS"
    )


def wipe_nodes() -> str:
    """Delete every node with a plain `DELETE`; a relationship left behind fails loudly (AC-9)."""
    return f"{PREFIX} MATCH (n) CALL (n) {{ DELETE n }} IN TRANSACTIONS OF {WIPE_BATCH_ROWS} ROWS"


def create_constraint(node: NodeMap) -> str:
    """Create the uniqueness constraint of one label's key under its fixed name (AC-10)."""
    return (
        f"{PREFIX} CREATE CONSTRAINT {name(node.constraint)} IF NOT EXISTS "
        f"FOR (n:{name(node.label)}) REQUIRE n.{name(node.key.property)} IS UNIQUE"
    )


def count_label(node: NodeMap) -> str:
    """`count(n)` and `count(DISTINCT n.<key>)` of one label (AC-15)."""
    return (
        f"{PREFIX} MATCH (n:{name(node.label)}) "
        f"RETURN count(n) AS count, count(DISTINCT n.{name(node.key.property)}) AS distinct"
    )


# `count(r)` and `count(DISTINCT r.edgeId)` per relationship type (AC-15), in
# one read: a type with no rows is simply absent (and counted 0), where a
# query naming it would draw a warning, and a type outside the 24 shows up.
COUNT_BY_TYPE = (
    f"{PREFIX} MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count, "
    f"count(DISTINCT r.{EDGE_ID_PROPERTY}) AS distinct"
)
COUNT_NODES = f"{PREFIX} MATCH (n) RETURN count(n) AS count"
COUNT_RELATIONSHIPS = f"{PREFIX} MATCH ()-[r]->() RETURN count(r) AS count"
SHOW_CONSTRAINTS = (
    f"{PREFIX} SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties"
)
SHOW_INDEXES = (
    f"{PREFIX} SHOW INDEXES "
    "YIELD name, type, entityType, labelsOrTypes, properties, owningConstraint"
)
SHOW_DATABASE = (
    f"{PREFIX} SHOW DATABASES YIELD name, access, currentStatus WHERE name = $name "
    "RETURN access, currentStatus"
)
SERVER_VERSION = (
    f"{PREFIX} CALL dbms.components() YIELD name, versions "
    "WHERE name = 'Neo4j Kernel' RETURN versions[0] AS version"
)
