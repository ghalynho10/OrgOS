"""The graph store's schema description, rendered from the mapping and the meanings (spec 0012).

`render_graph_description` is pure. Its structure comes from `mapping.toml`,
which spec 0010 AC-3 and AC-5 already hold the live graph to (AC-8); its words
come from `meanings.toml`, so the SQL description says the same things in the
same sentences. A node or relationship of table `t` uses the meaning key `t`,
and its property from column `c` the key `t.c`, the same keys the SQL
description uses for the same `model_int` fields.
"""

from __future__ import annotations

from orgos.agent.meanings import (
    GRAPH_BOOKKEEPING,
    LINK_TYPE_KEY,
    VALUE_KEYS,
    Description,
    Meanings,
    ValueLists,
    general_section,
    meaning_text,
    values_section,
)
from orgos.graphstore.mapping import GraphMapping, NodeMap, RelationshipMap, link_type

ENDS = ("start", "end")


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
