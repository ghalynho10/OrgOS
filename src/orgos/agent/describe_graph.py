"""The graph store's schema description, rendered from the mapping and the meanings (spec 0012).

`render_graph_description` is pure. Its structure comes from `mapping.toml`,
which spec 0010 AC-3 and AC-5 already hold the live graph to (AC-8); its words
come from `meanings.toml`, so the SQL description says the same things in the
same sentences. A node or relationship of table `t` uses the meaning key `t`,
and its property from column `c` the key `t.c`, the same keys the SQL
description uses for the same `model_int` fields. `read_graph_values` is the
only function here that touches Neo4j, through the one graph executor; every
label, type and property name it writes into Cypher comes from the mapping.
"""

from __future__ import annotations

from neo4j import Driver

from orgos.agent.meanings import (
    GRAPH_BOOKKEEPING,
    LINK_TYPE_KEY,
    VALUE_KEYS,
    Description,
    LinkWording,
    Meanings,
    ValueLists,
    general_section,
    meaning_text,
    values_section,
)
from orgos.graphstore.mapping import GraphMapping, NodeMap, RelationshipMap, link_type
from orgos.graphstore.query import run_cypher
from orgos.graphstore.statements import PREFIX, name

ENDS = ("start", "end")


def _property(rel: RelationshipMap, column: str) -> str:
    return name(next(c.property for c in rel.columns if c.column == column))


def read_graph_values(driver: Driver, mapping: GraphMapping, *, timeout_s: float) -> ValueLists:
    """Read the distinct listed values of `:Issue` and every link type's wording."""
    issue = mapping.node("Issue")
    by_column = {column.column: column.property for column in issue.columns}
    values = {}
    for key in VALUE_KEYS:
        prop = name(by_column[key.partition(".")[2]])
        statement = (
            f"{PREFIX} MATCH (i:{name(issue.label)}) WHERE i.{prop} IS NOT NULL"
            f" RETURN DISTINCT i.{prop} AS value ORDER BY value"
        )
        rows = run_cypher(driver, statement, timeout_s=timeout_s).rows
        values[key] = tuple(str(row["value"]) for row in rows)
    rel = next(rel for rel in mapping.relationships if rel.type_column is not None)
    by_type = {kind: link for link, kind in rel.types}
    outward, inward = _property(rel, "outward_label"), _property(rel, "inward_label")
    statement = (
        f"{PREFIX} MATCH ()-[r:{'|'.join(name(kind) for kind in rel.relationship_types)}]->()"
        f" RETURN DISTINCT type(r) AS kind, r.{outward} AS outward, r.{inward} AS inward"
        " ORDER BY kind, outward, inward"
    )
    links = tuple(
        LinkWording(by_type[str(row["kind"])], row["outward"], row["inward"])
        for row in run_cypher(driver, statement, timeout_s=timeout_s).rows
    )
    return ValueLists(values=values, link_types=links)


def _node_lines(node: NodeMap, meanings: Meanings) -> tuple[list[str], set[str]]:
    lines = [f"(:{node.label}): {meaning_text(meanings, node.table)}"]
    keys = {node.table}
    key = f"{node.table}.{node.key.column}"
    lines.append(f"- {node.key.property} {node.key.type}, unique: {meaning_text(meanings, key)}")
    keys.add(key)
    for column in node.columns:
        if column.property in GRAPH_BOOKKEEPING:
            continue
        key = f"{node.table}.{column.column}"
        lines.append(f"- {column.property} {column.type}: {meaning_text(meanings, key)}")
        keys.add(key)
    return lines, keys


def _relationship_lines(rel: RelationshipMap, meanings: Meanings) -> tuple[list[str], set[str]]:
    kind = rel.type if rel.type is not None else f"<one of the {len(rel.types)} link types>"
    lines = [
        f"(:{rel.start.label})-[:{kind}]->(:{rel.end.label}): {meaning_text(meanings, rel.table)}"
    ]
    keys = {rel.table}
    if rel.type_column is not None:
        lines.append(f"- type: {meaning_text(meanings, LINK_TYPE_KEY)}")
        keys.add(LINK_TYPE_KEY)
    for end in ENDS:
        key = f"{rel.table}.{end}"
        if key in meanings.fields:
            lines.append(f"- {end}: {meaning_text(meanings, key)}")
            keys.add(key)
    for column in rel.columns:
        if column.property in GRAPH_BOOKKEEPING:
            continue
        key = f"{rel.table}.{column.column}"
        lines.append(f"- {column.property} {column.type}: {meaning_text(meanings, key)}")
        keys.add(key)
    return lines, keys


def _field_names(mapping: GraphMapping) -> dict[str, str]:
    issue = mapping.node("Issue")
    by_column = {column.column: column.property for column in issue.columns}
    names = {key: f"Issue.{by_column[key.partition('.')[2]]}" for key in VALUE_KEYS}
    return names | {LINK_TYPE_KEY: "the link relationship type"}


def render_graph_description(
    mapping: GraphMapping, meanings: Meanings, values: ValueLists
) -> Description:
    """The graph schema description: the general sentences, nodes, relationships, value lists."""
    lines = ["Schema description: Neo4j, Cypher 25", "", *general_section(meanings)]
    keys: set[str] = set()
    lines.extend(("", "Nodes"))
    for node in mapping.nodes:
        node_lines, node_keys = _node_lines(node, meanings)
        lines.extend(node_lines)
        keys |= node_keys
    lines.extend(("", "Relationships"))
    for rel in mapping.relationships:
        rel_lines, rel_keys = _relationship_lines(rel, meanings)
        lines.extend(rel_lines)
        keys |= rel_keys
    lines.extend(
        (
            "",
            *values_section(
                values, _field_names(mapping), lambda name: f'"{name}" as :{link_type(name)}'
            ),
        )
    )
    return Description(text="\n".join(lines), keys=frozenset(keys))
