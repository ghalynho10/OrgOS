"""Fast checks of the temporal check and the value level compare (spec 0010 AC-16, AC-18).

No database: the rows are `model_int` rows as psycopg returns them, and the
graph side is what the reads return, a driver `DateTime` included.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from neo4j.time import DateTime

from orgos.graphstore.checks import Temporal, temporal_problems
from orgos.graphstore.compare import (
    compare_nodes,
    compare_relationships,
    comparison,
)
from orgos.graphstore.mapping import RelationshipMap, datetime_properties, load_mapping
from orgos.graphstore.statements import bad_timezones

MAPPING = load_mapping()
PERSON = MAPPING.node("Person")
ISSUE = MAPPING.node("Issue")


def _relationship(table: str) -> RelationshipMap:
    return next(rel for rel in MAPPING.relationships if rel.table == table)


LOGGED_TIME = _relationship("logged_time")
ISSUE_LINK = _relationship("issue_link")
# Two hours east of UTC, so the compare must use the converted instant.
EAST = timezone(timedelta(hours=2))
LOGGED_AT = datetime(2013, 1, 1, 12, 0, tzinfo=EAST)


# covers: AC-16 (every datetime property of the mapping is checked, pinned by name)
def test_the_temporal_check_reads_the_four_datetime_properties() -> None:
    names = {target.name() for target in datetime_properties(MAPPING)}
    assert names == {
        "Issue.createdAt",
        "Issue.resolutionDate",
        "Issue.dueAt",
        "LOGGED_TIME.loggedAt",
    }


def test_the_temporal_statement_reads_nodes_and_relationships() -> None:
    targets = {target.name(): target for target in datetime_properties(MAPPING)}
    node = bad_timezones(targets["Issue.createdAt"])
    assert node.startswith("CYPHER 25 MATCH (x:Issue) WHERE x.createdAt IS NOT NULL")
    # A value that is not a zoned datetime at all (local, or a string) counts as bad.
    assert "x.createdAt IS :: ZONED DATETIME THEN x.createdAt.timezone <> 'Z' ELSE true END" in node
    assert "collect(x.issueKey)[..20]" in node
    relationship = bad_timezones(targets["LOGGED_TIME.loggedAt"])
    assert relationship.startswith("CYPHER 25 MATCH ()-[x:LOGGED_TIME]->()")
    assert "collect(x.edgeId)[..20]" in relationship


def test_temporal_passes_when_every_value_is_in_the_z_form() -> None:
    clean = Temporal(bad={"Issue.createdAt": (0, ())}, default_timezone="Z")
    assert temporal_problems(clean) == ()


def test_temporal_names_values_not_in_the_z_form() -> None:
    found = Temporal(bad={"Issue.createdAt": (2, ("ABC-1", "ABC-2"))}, default_timezone="Z")
    assert temporal_problems(found) == (
        "Issue.createdAt: 2 values not in the Z form (ABC-1, ABC-2)",
    )


def test_temporal_names_a_server_default_not_in_the_z_form() -> None:
    assert temporal_problems(Temporal(bad={}, default_timezone="Etc/UTC")) == (
        "datetime() with no zone is Etc/UTC, not Z",
    )


def _person(key: str, **changes: Any) -> dict[str, Any]:
    row = {
        "person_key": key,
        "login_name": f"{key}_login",
        "is_active": True,
        "source_doc_id": f"doc-{key}",
        "load_id": "L1",
    }
    return row | changes


def _person_node(key: str, **changes: Any) -> dict[str, Any]:
    props = {
        "personKey": key,
        "loginName": f"{key}_login",
        "isActive": True,
        "sourceDocId": f"doc-{key}",
        "loadId": "L1",
    }
    return props | changes


# covers: AC-18 (every node property compared, an absent one included, both directions)
def test_compare_passes_faithful_nodes() -> None:
    rows = [_person("p1"), _person("p2", login_name=None)]
    found = [_person_node("p1"), {k: v for k, v in _person_node("p2").items() if k != "loginName"}]
    assert compare_nodes(PERSON, rows, found) == []


def test_compare_names_a_missing_and_an_extra_node() -> None:
    rows = [_person("p1"), _person("p2")]
    found = [_person_node("p1"), _person_node("p3")]
    assert compare_nodes(PERSON, rows, found) == [
        ("missing node", "Person p2"),
        ("extra node", "Person p3"),
    ]


def test_compare_names_a_changed_an_absent_and_an_unexpected_property() -> None:
    rows = [_person("p1"), _person("p2"), _person("p3", login_name=None)]
    found = [
        _person_node("p1", sourceDocId="other"),
        {k: v for k, v in _person_node("p2").items() if k != "loginName"},
        _person_node("p3"),
    ]
    assert compare_nodes(PERSON, rows, found) == [
        ("node property", "Person p1 sourceDocId"),
        ("node property", "Person p2 loginName"),
        ("node property", "Person p3 loginName"),
    ]


def test_compare_tells_a_boolean_from_an_integer() -> None:
    found = [_person_node("p1", isActive=1)]
    assert compare_nodes(PERSON, [_person("p1")], found) == [
        ("node property", "Person p1 isActive")
    ]


def test_compare_reads_a_driver_datetime_as_the_same_instant() -> None:
    row = {
        "issue_key": "ABC-1",
        "created_at": LOGGED_AT,
        "resolution_date": None,
        "due_at": None,
        "time_original_estimate_s": 3600,
        "resolution_name": None,
        "issue_type_name": "Bug",
        "priority_name": None,
        "status_name": "Open",
        "source_doc_id": "doc-1",
        "load_id": "L1",
    }
    node = {
        "issueKey": "ABC-1",
        "createdAt": DateTime.from_native(LOGGED_AT.astimezone(UTC)),
        "timeOriginalEstimateS": 3600,
        "issueTypeName": "Bug",
        "statusName": "Open",
        "sourceDocId": "doc-1",
        "loadId": "L1",
    }
    assert compare_nodes(ISSUE, [row], [node]) == []
    later = {**node, "createdAt": DateTime.from_native(LOGGED_AT + timedelta(seconds=1))}
    assert compare_nodes(ISSUE, [row], [later]) == [("node property", "Issue ABC-1 createdAt")]


def _logged(edge_id: str, src: str = "p1", dst: str = "ABC-1") -> dict[str, Any]:
    return {
        "edge_id": edge_id,
        "src_id": src,
        "dst_id": dst,
        "history_id": f"h-{edge_id}",
        "seconds": 60,
        "logged_at": LOGGED_AT,
        "source_doc_id": "doc-1",
        "load_id": "L1",
    }


def _logged_found(
    edge_id: str, src: str | None = "p1", dst: str | None = "ABC-1", **props: Any
) -> dict[str, Any]:
    return {
        "type": "LOGGED_TIME",
        "src": src,
        "dst": dst,
        "props": {
            "edgeId": edge_id,
            "historyId": f"h-{edge_id}",
            "seconds": 60,
            "loggedAt": DateTime.from_native(LOGGED_AT.astimezone(UTC)),
            "sourceDocId": "doc-1",
            "loadId": "L1",
        }
        | props,
    }


# covers: AC-18 (relationship properties, type and both end keys, both directions)
def test_compare_passes_faithful_relationships() -> None:
    rows = [_logged("e1"), _logged("e2", src="p2")]
    found = [_logged_found("e1"), _logged_found("e2", src="p2")]
    assert compare_relationships(LOGGED_TIME, rows, found) == []


def test_compare_names_a_reversed_relationship() -> None:
    # Read back with no label on the ends, a reversed one has null end keys.
    found = [_logged_found("e1", src=None, dst=None)]
    assert compare_relationships(LOGGED_TIME, [_logged("e1")], found) == [
        ("relationship ends", "logged_time e1: p1->ABC-1 expected, None->None"),
    ]


def test_compare_names_a_changed_relationship_property() -> None:
    found = [_logged_found("e1", seconds=61)]
    assert compare_relationships(LOGGED_TIME, [_logged("e1")], found) == [
        ("relationship property", "logged_time e1 seconds"),
    ]


def test_compare_names_a_repeated_edge_id_with_other_ends() -> None:
    found = [_logged_found("e1"), _logged_found("e1", src="p2")]
    assert compare_relationships(LOGGED_TIME, [_logged("e1")], found) == [
        ("repeated key", "logged_time e1"),
    ]


def test_compare_names_missing_and_extra_relationships() -> None:
    found = [_logged_found("e9")]
    assert compare_relationships(LOGGED_TIME, [_logged("e1")], found) == [
        ("missing relationship", "logged_time e1"),
        ("extra relationship", "logged_time e9"),
    ]


def test_compare_names_a_link_stored_under_the_wrong_type() -> None:
    row = {
        "edge_id": "l1",
        "src_id": "ABC-1",
        "dst_id": "ABC-2",
        "edge_type": "Blocker",
        "link_id": "100",
        "outward_label": "blocks",
        "inward_label": "is blocked by",
        "source_doc_id": "doc-1",
        "load_id": "L1",
    }
    props = {
        "edgeId": "l1",
        "linkId": "100",
        "outwardLabel": "blocks",
        "inwardLabel": "is blocked by",
        "sourceDocId": "doc-1",
        "loadId": "L1",
    }
    found = {"type": "BLOCKER", "src": "ABC-1", "dst": "ABC-2", "props": props}
    assert compare_relationships(ISSUE_LINK, [row], [found]) == []
    assert compare_relationships(ISSUE_LINK, [row], [{**found, "type": "CAUSE"}]) == [
        ("relationship type", "issue_link l1: BLOCKER expected, CAUSE"),
    ]


def test_comparison_counts_each_kind_and_names_twenty() -> None:
    differences = [("missing node", f"Person p{n}") for n in range(25)]
    result = comparison(3, 4, [*differences, ("extra node", "Person q")])
    assert result.differences["missing node"].count == 25
    assert len(result.differences["missing node"].shown) == 20
    assert result.problems[0].endswith("Person p19 and 5 more)")
    assert result.problems[1] == "extra node: 1 (Person q)"
    assert result.record()["nodes_compared"] == 3
    assert comparison(3, 4, []).problems == ()
