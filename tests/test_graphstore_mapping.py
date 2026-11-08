"""Fast checks of the graph mapping, the conversion, the statements and the compose command.

Spec 0010 AC-4, AC-11, AC-13 and AC-23, and spec 0008 AC-1, AC-4, AC-5, AC-7,
AC-8, AC-10, AC-11, AC-15 and AC-22. No database: the mapping is checked
against the columns and types `dbt/models/model_int/model_int.yml` declares.
"""

import os
import tomllib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from orgos.config import compose_subprocess_env
from orgos.graphstore import GraphStoreError
from orgos.graphstore.compose import access_name, compose_command, read_only_value
from orgos.graphstore.convert import convert_value, node_row, relationship_row, type_of
from orgos.graphstore.mapping import (
    MAPPING_FILE,
    Column,
    End,
    GraphMapping,
    RelationshipMap,
    coverage_problems,
    issue_pair_problems,
    load_mapping,
    mapping_problems,
    parse_mapping,
)
from orgos.graphstore.statements import (
    create_constraint,
    name,
    node_merge,
    relationship_merge,
    wipe_nodes,
    wipe_relationships,
)

ROOT = Path(__file__).resolve().parents[1]
DBT_YAML = yaml.safe_load(
    (ROOT / "dbt" / "models" / "model_int" / "model_int.yml").read_text(encoding="utf-8")
)
MODELS = {model["config"]["alias"]: model for model in DBT_YAML["models"]}
DECLARED = {
    table: {column["name"]: column["data_type"] for column in model["columns"]}
    for table, model in MODELS.items()
}
MAPPING = load_mapping()

# Spec 0008 AC-8, in full.
LINK_TYPES = (
    "BLOCKER",
    "BONFIRE_TESTING",
    "CAUSE",
    "CLONERS",
    "DEPENDS",
    "DERIVED",
    "DETAIL",
    "DUPLICATE",
    "FOLLOWS",
    "PART",
    "REFERENCE",
    "REGRESSION",
    "RELATE",
    "RELATED",
    "RESOLVE",
    "SPLIT",
    "SUPERSESSION",
)


def _relationship(table: str) -> RelationshipMap:
    return next(rel for rel in MAPPING.relationships if rel.table == table)


def _with_relationship(mapping: GraphMapping, changed: RelationshipMap) -> GraphMapping:
    return replace(
        mapping,
        relationships=tuple(
            changed if rel.table == changed.table else rel for rel in mapping.relationships
        ),
    )


# covers: spec 0008 AC-1 (all 12 tables mapped, 4 as labels and 8 as relationships, none excluded)
def test_mapping_names_the_12_tables() -> None:
    assert sorted(MAPPING.tables) == sorted(DECLARED)
    assert len(MAPPING.nodes) == 4
    assert len(MAPPING.relationships) == 8
    assert MAPPING.excluded == ()
    assert coverage_problems(MAPPING, DECLARED) == ()


def test_coverage_names_a_table_neither_mapped_nor_excluded() -> None:
    declared = {**DECLARED, "sprint": {"sprint_id": "text"}}
    assert coverage_problems(MAPPING, declared) == ("sprint is neither mapped nor excluded",)


def test_coverage_names_a_mapped_table_that_is_not_model_int() -> None:
    declared = {table: columns for table, columns in DECLARED.items() if table != "project"}
    assert coverage_problems(MAPPING, declared) == ("project is mapped but not a model_int table",)


# covers: spec 0008 AC-11 (every column a property or structural; nothing derived)
def test_coverage_names_a_column_that_is_neither_property_nor_structural() -> None:
    declared = {**DECLARED, "issue": {**DECLARED["issue"], "summary": "text"}}
    assert coverage_problems(MAPPING, declared) == (
        "issue.summary is neither a property nor structural",
    )


def test_coverage_names_a_property_that_is_not_a_declared_column() -> None:
    project = MAPPING.node("Project")
    derived = Column("issue_count", "issueCount", "Integer")
    mapping = replace(
        MAPPING,
        nodes=tuple(
            replace(node, columns=(*node.columns, derived)) if node is project else node
            for node in MAPPING.nodes
        ),
    )
    assert coverage_problems(mapping, DECLARED) == (
        "project.issue_count is mapped but not a declared column",
    )


def test_link_type_name_is_structural_only_on_issue_link() -> None:
    declared = {**DECLARED, "fixed_in": {**DECLARED["fixed_in"], "link_type_name": "text"}}
    assert coverage_problems(MAPPING, declared) == (
        "fixed_in.link_type_name is neither a property nor structural",
    )


# covers: spec 0008 AC-15 (text, boolean, bigint, timestamptz only; jsonb fails)
@pytest.mark.parametrize(
    ("data_type", "problem"),
    [
        ("bigint", "person.login_name is bigint but mapped as String"),
        ("jsonb", "person.login_name has type jsonb, which no property type allows"),
    ],
)
def test_coverage_names_a_type_the_conversion_table_does_not_give(
    data_type: str, problem: str
) -> None:
    declared = {**DECLARED, "person": {**DECLARED["person"], "login_name": data_type}}
    assert coverage_problems(MAPPING, declared) == (problem,)


# covers: spec 0008 AC-2, AC-3, AC-4, AC-10 (labels, the 7 fixed types, their directions)
def test_labels_types_and_directions_follow_spec_0008() -> None:
    assert [node.label for node in MAPPING.nodes] == ["Issue", "Project", "Person", "FixVersion"]
    assert [
        (rel.table, rel.type, rel.start.label, rel.end.label)
        for rel in MAPPING.relationships
        if rel.type is not None
    ] == [
        ("in_project", "IN_PROJECT", "Issue", "Project"),
        ("assigned_to", "ASSIGNED_TO", "Issue", "Person"),
        ("reported_by", "REPORTED_BY", "Issue", "Person"),
        ("created_by", "CREATED_BY", "Issue", "Person"),
        ("has_parent", "HAS_PARENT", "Issue", "Issue"),
        ("fixed_in", "FIXED_IN", "Issue", "FixVersion"),
        ("logged_time", "LOGGED_TIME", "Person", "Issue"),
    ]
    assert len(MAPPING.relationship_types) == 24


# covers: spec 0008 AC-13 (sourceDocId and loadId on every node and relationship)
def test_every_table_maps_its_provenance() -> None:
    columns = [(node.table, node.columns) for node in MAPPING.nodes] + [
        (rel.table, rel.columns) for rel in MAPPING.relationships
    ]
    for table, mapped in columns:
        pairs = {(column.column, column.property) for column in mapped}
        assert {("source_doc_id", "sourceDocId"), ("load_id", "loadId")} <= pairs, table


# covers: spec 0008 AC-22 (the fixed constraint names)
def test_constraints_have_their_fixed_names() -> None:
    assert [(node.label, node.key.property, node.constraint) for node in MAPPING.nodes] == [
        ("Issue", "issueKey", "issue_issuekey_unique"),
        ("Project", "projectKey", "project_projectkey_unique"),
        ("Person", "personKey", "person_personkey_unique"),
        ("FixVersion", "versionId", "fixversion_versionid_unique"),
    ]


# covers: spec 0008 AC-7, AC-8 (the 17 values of the dbt YAML, each to its type, no prefix)
def test_link_types_are_the_17_values_of_the_dbt_yaml() -> None:
    link = _relationship("issue_link")
    accepted = next(
        test["accepted_values"]["arguments"]["values"]
        for column in MODELS["issue_link"]["columns"]
        if column["name"] == "link_type_name"
        for test in column["data_tests"]
    )
    assert [value for value, _ in link.types] == accepted
    assert link.relationship_types == LINK_TYPES
    assert link.type_column == "edge_type"


def test_a_link_value_mapped_to_the_wrong_type_fails_the_load_checks() -> None:
    link = _relationship("issue_link")
    types = (("Blocker", "BLOCKS"), *link.types[1:])
    problems = mapping_problems(_with_relationship(MAPPING, replace(link, types=types)))
    assert problems == ("issue_link maps 'Blocker' to BLOCKS, expected BLOCKER",)


def test_a_missing_link_value_fails_the_load_checks() -> None:
    link = _relationship("issue_link")
    problems = mapping_problems(_with_relationship(MAPPING, replace(link, types=link.types[1:])))
    assert problems == ("issue_link lists 16 link types, expected 17",)


# covers: spec 0008 AC-5 (only HAS_PARENT and the 17 links run between two issues)
def test_only_has_parent_and_links_run_between_issues() -> None:
    assert issue_pair_problems(MAPPING) == ()
    clones = _relationship("has_parent")
    mapping = replace(
        MAPPING, relationships=(*MAPPING.relationships, replace(clones, type="CLONE_OF"))
    )
    assert issue_pair_problems(mapping) == ("CLONE_OF runs between two Issue nodes",)


# covers: AC-4 (the load time checks: table count, structural columns, names)
def test_the_mapping_file_passes_its_load_checks() -> None:
    assert mapping_problems(MAPPING) == ()


def test_a_twelfth_table_missing_fails_the_load_checks() -> None:
    mapping = replace(MAPPING, relationships=MAPPING.relationships[:-1])
    assert mapping_problems(mapping)[0].endswith("expected 12 distinct")


def test_a_structural_column_as_property_fails_the_load_checks() -> None:
    in_project = _relationship("in_project")
    changed = replace(
        in_project, columns=(*in_project.columns, Column("src_id", "srcId", "String"))
    )
    assert mapping_problems(_with_relationship(MAPPING, changed)) == (
        "in_project.src_id is structural and cannot be a property",
    )


def test_an_unknown_property_type_fails_the_load_checks() -> None:
    changed = replace(
        _relationship("logged_time"),
        columns=(Column("seconds", "seconds", "Float"),),
    )
    assert mapping_problems(_with_relationship(MAPPING, changed)) == (
        "logged_time.seconds has unknown type 'Float'",
    )


def test_an_end_that_is_not_a_mapped_key_fails_the_load_checks() -> None:
    changed = replace(_relationship("fixed_in"), end=End("FixVersion", "versionName"))
    assert mapping_problems(_with_relationship(MAPPING, changed)) == (
        "fixed_in end FixVersion.versionName is not a mapped node key",
    )


def test_a_name_that_is_not_an_identifier_fails_the_load_checks() -> None:
    changed = replace(_relationship("created_by"), type="CREATED BY")
    assert mapping_problems(_with_relationship(MAPPING, changed)) == (
        "'CREATED BY' is not a plain identifier",
    )


def test_parse_mapping_raises_with_every_problem() -> None:
    data = tomllib.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    data["excluded"] = ["project"]
    with pytest.raises(GraphStoreError, match="expected 12 distinct"):
        parse_mapping(data)


def test_load_mapping_names_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(GraphStoreError, match="Cannot read the graph mapping"):
        load_mapping(tmp_path / "mapping.toml")


def test_load_mapping_names_broken_toml(tmp_path: Path) -> None:
    path = tmp_path / "mapping.toml"
    path.write_text("[[node]\n", encoding="utf-8")
    with pytest.raises(GraphStoreError, match="is not valid TOML"):
        load_mapping(path)


def test_load_mapping_names_a_malformed_entry(tmp_path: Path) -> None:
    path = tmp_path / "mapping.toml"
    path.write_text('excluded = []\nrelationship = []\n[[node]]\ntable = "issue"\n', "utf-8")
    with pytest.raises(GraphStoreError, match="malformed"):
        load_mapping(path)


# covers: AC-13 (conversion)
STATUS = Column("status_name", "statusName", "String")
CREATED = Column("created_at", "createdAt", "datetime")
SECONDS = Column("seconds", "seconds", "Integer")
ACTIVE = Column("is_active", "isActive", "Boolean")


def test_a_null_becomes_an_absent_property() -> None:
    issue = MAPPING.node("Issue")
    row: dict[str, Any] = dict.fromkeys(issue.read_columns)
    row |= {
        "issue_key": "BAM-1",
        "created_at": datetime(2013, 1, 1, tzinfo=UTC),
        "status_name": "Open",
        "issue_type_name": "Bug",
        "source_doc_id": "d",
        "load_id": "L",
    }
    assert node_row(issue, row) == {
        "key": "BAM-1",
        "props": {
            "createdAt": datetime(2013, 1, 1, tzinfo=UTC),
            "issueTypeName": "Bug",
            "statusName": "Open",
            "sourceDocId": "d",
            "loadId": "L",
        },
    }


def test_an_empty_string_fails_naming_table_and_key() -> None:
    with pytest.raises(GraphStoreError, match=r"^issue row BAM-1: status_name is an empty string"):
        convert_value("", STATUS, "issue", "BAM-1")


def test_a_datetime_with_no_zone_fails_naming_table_and_key() -> None:
    with pytest.raises(
        GraphStoreError, match=r"^issue row BAM-1: created_at is a datetime with no"
    ):
        convert_value(datetime(2013, 1, 1), CREATED, "issue", "BAM-1")


def test_a_datetime_is_converted_to_utc() -> None:
    st_johns = timezone(timedelta(hours=-3, minutes=-30))
    value = convert_value(datetime(2013, 1, 1, 20, 30, tzinfo=st_johns), CREATED, "issue", "BAM-1")
    assert value == datetime(2013, 1, 2, 0, 0, tzinfo=UTC)
    assert isinstance(value, datetime)
    assert value.tzinfo is UTC


@pytest.mark.parametrize(
    ("value", "column", "found"),
    [
        (1, STATUS, "int"),
        ("2013-01-01", CREATED, "str"),
        (True, SECONDS, "bool"),
        (1, ACTIVE, "int"),
    ],
)
def test_a_value_of_the_wrong_type_fails(value: object, column: Column, found: str) -> None:
    message = f"is of Python type {found}, but the mapping declares"
    with pytest.raises(GraphStoreError, match=message):
        convert_value(value, column, "t", "k")


def test_relationship_row_carries_ends_edge_id_and_properties() -> None:
    logged = _relationship("logged_time")
    row = {
        "edge_id": "E1",
        "src_id": "alice",
        "dst_id": "BAM-1",
        "edge_type": "logged_time",
        "history_id": "H1",
        "seconds": -60,
        "logged_at": datetime(2013, 1, 1, tzinfo=UTC),
        "source_doc_id": "d",
        "load_id": "L",
    }
    assert relationship_row(logged, row) == {
        "src": "alice",
        "dst": "BAM-1",
        "edgeId": "E1",
        "props": {
            "historyId": "H1",
            "seconds": -60,
            "loggedAt": datetime(2013, 1, 1, tzinfo=UTC),
            "sourceDocId": "d",
            "loadId": "L",
        },
    }


def test_a_null_end_fails_naming_table_and_edge_id() -> None:
    row = {"edge_id": "E1", "src_id": "BAM-1", "dst_id": None}
    with pytest.raises(GraphStoreError, match=r"^in_project row E1: dst_id is None"):
        relationship_row(_relationship("in_project"), row)


# covers: AC-11 (an unlisted edge_type fails naming the table, the edge_id and the value)
def test_an_unlisted_edge_type_fails_naming_table_edge_id_and_value() -> None:
    link = _relationship("issue_link")
    assert type_of(link, {"edge_id": "L1", "edge_type": "Bonfire Testing"}) == "BONFIRE_TESTING"
    with pytest.raises(GraphStoreError, match=r"^issue_link row L1: edge_type 'Blocks' is not"):
        type_of(link, {"edge_id": "L1", "edge_type": "Blocks"})


# covers: AC-9, AC-10, AC-11 (the statements)
def test_node_merge_is_built_from_the_mapping() -> None:
    assert node_merge(MAPPING.node("Issue")) == (
        "CYPHER 25 UNWIND $rows AS row MERGE (n:Issue {issueKey: row.key}) SET n += row.props"
    )


def test_relationship_merge_matches_both_ends_by_key() -> None:
    rel = _relationship("in_project")
    assert relationship_merge("IN_PROJECT", rel.start, rel.end) == (
        "CYPHER 25 UNWIND $rows AS row MATCH (a:Issue {issueKey: row.src}) "
        "MATCH (b:Project {projectKey: row.dst}) "
        "MERGE (a)-[r:IN_PROJECT {edgeId: row.edgeId}]->(b) SET r += row.props"
    )


def test_wipe_deletes_relationships_then_nodes_without_detach() -> None:
    assert wipe_relationships() == (
        "CYPHER 25 MATCH ()-[r]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS"
    )
    assert wipe_nodes() == (
        "CYPHER 25 MATCH (n) CALL (n) { DELETE n } IN TRANSACTIONS OF 10000 ROWS"
    )
    assert "DETACH" not in wipe_nodes()


def test_constraint_uses_its_fixed_name_if_not_exists() -> None:
    assert create_constraint(MAPPING.node("FixVersion")) == (
        "CYPHER 25 CREATE CONSTRAINT fixversion_versionid_unique IF NOT EXISTS "
        "FOR (n:FixVersion) REQUIRE n.versionId IS UNIQUE"
    )


@pytest.mark.parametrize("bad", ["Issue) DETACH DELETE (m", "IN PROJECT", "", "1st", "a-b"])
def test_a_name_that_is_not_an_identifier_never_reaches_cypher(bad: str) -> None:
    with pytest.raises(GraphStoreError, match="is not a plain identifier"):
        name(bad)
    with pytest.raises(GraphStoreError):
        relationship_merge(bad, End("Issue", "issueKey"), End("Issue", "issueKey"))


# covers: AC-7, AC-8 (the compose command and its one variable)
def test_compose_command_recreates_neo4j_and_waits() -> None:
    assert compose_command() == (
        "docker",
        "compose",
        "--profile",
        "graph",
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "120",
        "neo4j",
    )


def test_read_only_value_and_access() -> None:
    assert (read_only_value(False), access_name(False)) == ("", "read-write")
    assert (read_only_value(True), access_name(True)) == ("neo4j", "read-only")


def test_compose_env_sets_exactly_one_variable_over_the_inherited_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGOS_NEO4J_READ_ONLY_DATABASES", "")
    env = compose_subprocess_env("ORGOS_NEO4J_READ_ONLY_DATABASES", "neo4j")
    assert env["ORGOS_NEO4J_READ_ONLY_DATABASES"] == "neo4j"
    assert {
        key: value for key, value in env.items() if key != "ORGOS_NEO4J_READ_ONLY_DATABASES"
    } == {
        key: value for key, value in os.environ.items() if key != "ORGOS_NEO4J_READ_ONLY_DATABASES"
    }
