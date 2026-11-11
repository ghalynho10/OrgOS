"""Equal care for the two schema descriptions (spec 0012 AC-8 to AC-10).

The SQL structure here is read from the `model` contract in `model.yml`, the
same tables, columns, keys and indexes the live catalog holds after a build;
the live read itself is covered by the integration tests. The graph structure
is the real `mapping.toml`. Value lists are made up: the tests check that
each one is printed, not what the store holds.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import tiktoken
import yaml

from orgos.agent.describe_graph import render_graph_description
from orgos.agent.describe_sql import ForeignKey, SqlColumn, SqlTable, render_sql_description
from orgos.agent.errors import AgentError
from orgos.agent.meanings import (
    GRAPH_BOOKKEEPING,
    SQL_BOOKKEEPING,
    VALUE_KEYS,
    Description,
    LinkWording,
    Meanings,
    ValueLists,
    load_meanings,
    parse_meanings,
)
from orgos.eval.models import read_toml
from orgos.graphstore.mapping import load_mapping

ROOT = Path(__file__).resolve().parents[1]
MODEL_TABLES = 7
SIZE_TOLERANCE = 0.20
MAPPING = load_mapping()
MEANINGS = load_meanings()
LINK_NAMES = tuple(name for rel in MAPPING.relationships for name, _ in rel.types)
VALUES = ValueLists(
    values={
        "issue.status_name": ("Open", "Closed", "In Progress"),
        "issue.issue_type_name": ("Bug", "Sub-task"),
        "issue.priority_name": ("Major", "Minor"),
        "issue.resolution_name": ("Fixed", "Won't Fix"),
    },
    link_types=tuple(
        LinkWording(name, f"outward wording {n}", f"inward wording {n}")
        for n, name in enumerate(LINK_NAMES)
    ),
)


def _ref(text: str) -> str:
    match = re.fullmatch(r"ref\('(\w+)'\)", text)
    assert match, text
    return match.group(1)


def _table(model: dict[str, Any]) -> SqlTable:
    columns, primary, foreign = [], [], []
    for column in model["columns"]:
        kinds = {constraint["type"]: constraint for constraint in column.get("constraints", [])}
        if "primary_key" in kinds:
            primary.append(column["name"])
        if "foreign_key" in kinds:
            key = kinds["foreign_key"]
            foreign.append(ForeignKey((column["name"],), _ref(key["to"]), tuple(key["to_columns"])))
        not_null = "not_null" in kinds or "primary_key" in kinds
        columns.append(SqlColumn(column["name"], column["data_type"], not_null))
    for constraint in model.get("constraints", []):
        if constraint["type"] == "primary_key":
            primary.extend(constraint["columns"])
            columns = [
                SqlColumn(c.name, c.data_type, c.not_null or c.name in constraint["columns"])
                for c in columns
            ]
    indexes = tuple(tuple(index["columns"]) for index in model.get("config", {}).get("indexes", []))
    return SqlTable(model["name"], tuple(columns), tuple(primary), tuple(foreign), indexes)


SQL_TABLES = tuple(
    _table(model)
    for model in yaml.safe_load((ROOT / "dbt/models/model/model.yml").read_text())["models"]
)


def _sql(meanings: Meanings = MEANINGS, values: ValueLists = VALUES) -> Description:
    return render_sql_description(SQL_TABLES, meanings, values)


def _graph(meanings: Meanings = MEANINGS, values: ValueLists = VALUES) -> Description:
    return render_graph_description(MAPPING, meanings, values)


def test_the_fixture_holds_the_seven_model_tables() -> None:
    assert len(SQL_TABLES) == MODEL_TABLES
    assert sum(len(table.columns) for table in SQL_TABLES) > 30


# --- Every field is described (AC-9) ---


def test_every_model_column_is_in_the_sql_description() -> None:
    text = _sql().text
    for table in SQL_TABLES:
        block = text.split(f"\nmodel.{table.name}: ", 1)[1].split("\nmodel.", 1)[0]
        for column in table.columns:
            if column.name not in SQL_BOOKKEEPING:
                assert f"\n- {column.name} {column.data_type}" in block, column.name


def test_every_label_property_and_relationship_is_in_the_graph_description() -> None:
    text = _graph().text
    for node in MAPPING.nodes:
        assert f"(:{node.label}): " in text
        for column in (node.key, *node.columns):
            if column.property not in GRAPH_BOOKKEEPING:
                assert f"- {column.property} {column.type}" in text, column.property
    for rel in MAPPING.relationships:
        for kind in rel.relationship_types:
            assert f":{kind}" in text, kind
        for column in rel.columns:
            if column.property not in GRAPH_BOOKKEEPING:
                assert f"- {column.property} {column.type}" in text, column.property


@pytest.mark.parametrize(
    ("render", "names"),
    [(_sql, SQL_BOOKKEEPING | GRAPH_BOOKKEEPING), (_graph, SQL_BOOKKEEPING | GRAPH_BOOKKEEPING)],
)
def test_the_bookkeeping_fields_are_named_once_in_one_sentence(
    render: Any, names: frozenset[str]
) -> None:
    text = render().text
    sentence = next(line for line in text.splitlines() if "bookkeeping" in line)
    for name in names:
        assert len(re.findall(rf"\b{name}\b", text)) == 1, name
        assert re.search(rf"\b{name}\b", sentence), name


def test_both_descriptions_use_the_same_meaning_keys_and_all_of_them() -> None:
    sql, graph = _sql(), _graph()
    assert sql.keys == graph.keys
    assert sql.keys == frozenset(MEANINGS.fields)


def test_the_two_descriptions_are_within_20_percent_in_tokens() -> None:
    encoding = tiktoken.get_encoding("o200k_base")
    sql = len(encoding.encode(_sql().text))
    graph = len(encoding.encode(_graph().text))
    assert abs(sql - graph) <= SIZE_TOLERANCE * min(sql, graph), (sql, graph)


# --- The same sentences and lists in both (AC-10) ---


def test_both_carry_every_general_sentence_word_for_word() -> None:
    sql, graph = _sql().text, _graph().text
    for sentence in MEANINGS.general:
        assert f"- {sentence}\n" in sql
        assert f"- {sentence}\n" in graph
    for word in ("outward wording", "seconds > 0", "UTC", "Z form", "bot", "missing", "folded"):
        assert any(word in sentence or word == "folded" for sentence in MEANINGS.general), word


def test_both_carry_every_field_meaning_with_its_dump_paths() -> None:
    sql, graph = _sql().text, _graph().text
    for meaning in MEANINGS.fields.values():
        assert meaning.meaning in sql, meaning.key
        assert meaning.meaning in graph, meaning.key
        for path in meaning.dump:
            assert f"`{path}`" in sql, path
            assert f"`{path}`" in graph, path


def test_both_list_every_value_and_every_link_wording() -> None:
    sql, graph = _sql().text, _graph().text
    for key in VALUE_KEYS:
        for value in VALUES.values[key]:
            assert f'"{value}"' in sql, value
            assert f'"{value}"' in graph, value
    assert len(VALUES.link_types) == 17
    for link in VALUES.link_types:
        wording = f'"{link.outward}" / "{link.inward}"'
        assert f'- "{link.name}": {wording}' in sql
        assert re.search(rf'- "{link.name}" as :\w+: {re.escape(wording)}', graph)


def test_every_question_field_path_is_in_both() -> None:
    questions = read_toml(ROOT / "eval/questions.toml")["question"]
    paths = {path for question in questions for path in question["fields"]}
    assert paths
    sql, graph = _sql().text, _graph().text
    for path in sorted(paths):
        assert f"`{path}`" in sql, path
        assert f"`{path}`" in graph, path


# --- Pure and sorted (AC-8) ---


def test_the_same_inputs_in_another_order_give_the_same_text() -> None:
    shuffled_tables = tuple(
        SqlTable(t.name, t.columns, t.primary_key, t.foreign_keys[::-1], t.indexes[::-1])
        for t in reversed(SQL_TABLES)
    )
    shuffled_values = ValueLists(
        values={key: tuple(reversed(value)) for key, value in VALUES.values.items()},
        link_types=tuple(reversed(VALUES.link_types)),
    )
    sql = render_sql_description(shuffled_tables, MEANINGS, shuffled_values)
    assert sql == _sql()
    assert _graph(values=shuffled_values) == _graph()


def test_a_column_with_no_meaning_stops_the_render() -> None:
    fields = {key: value for key, value in MEANINGS.fields.items() if key != "issue.due_at"}
    meanings = Meanings(general=MEANINGS.general, fields=fields)
    with pytest.raises(AgentError, match=r"no meaning for 'issue\.due_at'"):
        _sql(meanings)
    with pytest.raises(AgentError, match=r"no meaning for 'issue\.due_at'"):
        _graph(meanings)


def test_a_missing_value_is_shown_as_missing() -> None:
    values = ValueLists(values=VALUES.values, link_types=(LinkWording("Relate", None, "x"),))
    assert '- "Relate": (missing) / "x"' in _sql(values=values).text


# --- The meanings file ---


def test_a_meanings_file_with_gaps_names_each_problem() -> None:
    with pytest.raises(AgentError) as error:
        parse_meanings(
            {
                "general": {"bots": "x", "extra": "y"},
                "field": [
                    {"key": "a", "meaning": "A."},
                    {"key": "a", "meaning": "Again."},
                    {"key": "b"},
                    {"key": "c", "meaning": "C.", "dump": "fields.c"},
                ],
            }
        )
    message = str(error.value)
    for problem in (
        "has no sentence 'utc'",
        "'extra' is not a known sentence",
        "a is listed twice",
        "field 3 needs a key and a meaning",
        "c: dump must be a list",
    ):
        assert problem in message
