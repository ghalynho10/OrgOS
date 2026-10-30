"""Unit tests for the free text split (spec 0005, AC-16, AC-17).

Every case of the spec's Free text split scenario, on synthetic pymongo
decoded documents (datetimes as `datetime`, numbers as `int` or `float`).
"""

import datetime as dt
from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.fields import (
    ALWAYS_USABLE,
    HISTORY_MEMBERS,
    TIME_ENTRY_PAIR,
    field_classes,
    is_date,
    is_empty,
    kind,
    pair_path,
    passes_text_guard,
    summarize,
    walk,
    walked_values,
)


def issue(fields: dict[str, Any] | None = None, **top: Any) -> dict[str, Any]:
    return {"key": "A-1", "id": "1", "fields": fields or {}, **top}


def history(*items: dict[str, Any], **members: Any) -> dict[str, Any]:
    return {"id": "10", "created": "2020-01-01T00:00:00.000+0000", **members, "items": list(items)}


def item(name: str, **sides: Any) -> dict[str, Any]:
    return {"field": name, "fieldtype": "custom", **sides}


def with_changelog(*histories: dict[str, Any]) -> dict[str, Any]:
    return issue(changelog={"histories": list(histories)})


def classes(*issues: dict[str, Any]) -> dict[str, tuple[bool, str]]:
    """Path to (usable, reason) for every listed path."""
    summaries = summarize(pair for doc in issues for pair in walked_values(doc))
    found = field_classes(summaries)
    listed = {i.path: (True, i.reason) for i in found.usable}
    for i in found.free_text:
        assert i.path not in listed
        listed[i.path] = (False, i.reason)
    return listed


def one_per_issue(path_value: list[Any], field: str) -> list[dict[str, Any]]:
    return [issue({field: value}) for value in path_value]


def usable(listed: dict[str, tuple[bool, str]], path: str) -> bool:
    return listed[path][0]


# The sub rules


@pytest.mark.parametrize(
    "value",
    [
        "2020-01-31",
        "2020-01-31T10:00:00+0000",
        "2020-01-31T10:00:00.123+00:00",
        "2020-01-31T10:00:00Z",
        "2020-01-31T10:00:00-0530",
        dt.datetime(2020, 1, 31, tzinfo=dt.UTC),
    ],
)
def test_dates(value: object) -> None:
    assert is_date(value)


@pytest.mark.parametrize("value", ["2020-01-31T10:00:00", "31/01/2020", "2020-1-31", 20200131])
def test_not_dates(value: object) -> None:
    assert not is_date(value)


def test_the_text_guard() -> None:
    assert passes_text_guard("x" * 100)
    assert not passes_text_guard("x" * 101)
    assert not passes_text_guard("a\nb")
    assert not passes_text_guard("a\u2028b")
    assert not passes_text_guard("a\x1cb")
    assert not passes_text_guard("trailing\n")
    assert passes_text_guard(" padded ")  # nothing is stripped first
    assert passes_text_guard(12345)


@pytest.mark.parametrize("value", [None, "", [], {}])
def test_empty_values(value: object) -> None:
    assert is_empty(value)


def test_zero_and_false_are_not_empty() -> None:
    assert not is_empty(0)
    assert not is_empty(False)


def test_a_bool_is_never_a_number() -> None:
    assert kind(True) == "boolean"
    assert kind(1) == kind(1.5) == "number"


def test_walk_flattens_one_level_per_bracket() -> None:
    assert list(walk("fields.m", [[1, 2], [3]])) == [
        ("fields.m[][]", 1),
        ("fields.m[][]", 2),
        ("fields.m[][]", 3),
    ]


# The Free text split scenario


def test_a_date_path_with_every_offset_form_is_usable() -> None:
    values = [
        "2020-01-01T00:00:00.000+0000",
        "2020-01-01T00:00:00+00:00",
        "2020-01-01T00:00:00Z",
        dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
    ]
    listed = classes(*one_per_issue(values, "when"))
    assert listed["fields.when"] == (True, "date")


def test_fifty_short_values_are_categorical() -> None:
    listed = classes(*one_per_issue([f"v{i}" for i in range(50)], "cat"))
    assert listed["fields.cat"] == (True, "categorical")


def test_fifty_one_values_are_free_text() -> None:
    listed = classes(*one_per_issue([f"v{i}" for i in range(51)], "cat"))
    assert listed["fields.cat"] == (False, "more than 50 distinct values")


def test_fifty_values_with_one_too_long_are_free_text() -> None:
    values = [f"v{i}" for i in range(49)] + ["x" * 101]
    assert not usable(classes(*one_per_issue(values, "cat")), "fields.cat")


@pytest.mark.parametrize("brk", ["\n", "\u2028"])
def test_fifty_values_with_a_line_break_are_free_text(brk: str) -> None:
    values = [f"v{i}" for i in range(49)] + [f"a{brk}b"]
    assert classes(*one_per_issue(values, "cat"))["fields.cat"] == (False, "fails the text guard")


def test_numbers_mixed_with_strings_are_free_text() -> None:
    listed = classes(*one_per_issue([1, "two", 3.5], "mix"))
    assert listed["fields.mix"] == (False, "mixed types")


def test_empty_values_are_ignored_and_an_all_empty_path_is_not_listed() -> None:
    listed = classes(*one_per_issue([1, "", None, [], {}, 2], "n"), issue({"hollow": ""}))
    assert listed["fields.n"] == (True, "number")
    assert not any(path.startswith("fields.hollow") for path in listed)
    assert "fields.void" not in classes(issue({"void": {}}), issue({"void": []}))


def test_an_array_of_arrays_is_classed_at_its_flattened_path() -> None:
    listed = classes(issue({"m": [[1, 2], [3]]}))
    assert listed["fields.m[][]"] == (True, "number")


def test_objects_and_arrays_of_objects_are_never_listed() -> None:
    listed = classes(
        issue({"status": {"name": "Open"}, "components": [{"name": "UI"}, {"name": "API"}]})
    )
    assert "fields.status" not in listed
    assert "fields.components[]" not in listed
    assert "fields.components" not in listed
    assert listed["fields.components[].name"] == (True, "name")


def test_key_and_id_members_are_structured_unguarded() -> None:
    docs = [
        {
            "key": f"A-{i}",
            "id": str(i),
            "fields": {
                "assignee": {"key": f"person{i}"},
                "issuelinks": [{"id": str(10_000 + i)}],
            },
        }
        for i in range(3000)
    ]
    docs.append(issue({"reporter": {"key": "a\nb"}}))
    listed = classes(*docs)
    for path in ("key", "id", "fields.assignee.key", "fields.issuelinks[].id"):
        assert listed[path] == (True, "key or id")
    assert listed["fields.reporter.key"] == (True, "key or id")


def test_a_long_name_member_is_free_text() -> None:
    listed = classes(issue({"version": {"name": "x" * 101}}))
    assert listed["fields.version.name"] == (False, "fails the text guard")


def test_a_status_description_of_prose_is_free_text() -> None:
    prose = "The issue is open and ready for the assignee to start work on it, " * 2
    listed = classes(issue({"status": {"name": "Open", "description": prose}}))
    assert not usable(listed, "fields.status.description")
    assert usable(listed, "fields.status.name")


def test_id_self_expand_and_rendered_fields_are_never_walked() -> None:
    doc = issue(
        {"a": 1},
        _id="abc",
        self="https://example.invalid/1",
        expand="changelog",
        renderedFields={"description": "<p>x</p>", "a": "1"},
    )
    doc["changelog"] = {"startAt": 0, "total": 1, "histories": []}
    listed = classes(doc)
    assert set(listed) == {"key", "id", "fields.a", *ALWAYS_USABLE}


def test_an_item_name_with_a_space_and_a_capital_is_kept_verbatim() -> None:
    doc = with_changelog(history(item("Fix Version", **{"from": "1", "to": "2"})))
    assert "changelog.items[field=Fix Version].from/to" in classes(doc)
    assert pair_path("Fix Version", ("fromString", "toString")) == (
        "changelog.items[field=Fix Version].fromString/toString"
    )


def test_a_raw_pair_with_thousands_of_short_values_is_structured() -> None:
    items = [item("Status", **{"from": str(i), "to": str(i + 1)}) for i in range(3000)]
    listed = classes(with_changelog(history(*items)))
    assert listed["changelog.items[field=Status].from/to"] == (True, "raw changelog pair")


def test_a_raw_pair_holding_a_line_break_is_free_text() -> None:
    doc = with_changelog(history(item("Notes", **{"from": "a", "to": "b\nc"})))
    assert not usable(classes(doc), "changelog.items[field=Notes].from/to")


def test_a_display_pair_follows_the_leaf_rules() -> None:
    many = [item("Rank", fromString=f"r{i}", toString=f"r{i + 1}") for i in range(60)]
    few = [item("Status", fromString="Open", toString="Closed")]
    listed = classes(with_changelog(history(*many, *few)))
    assert not usable(listed, "changelog.items[field=Rank].fromString/toString")
    assert listed["changelog.items[field=Status].fromString/toString"] == (True, "categorical")


def test_a_pair_with_one_side_empty_is_classed_on_the_other() -> None:
    doc = with_changelog(
        history(
            item("Owner", **{"from": None, "to": "a\nb"}),
            item("Label", **{"from": "", "to": "x"}, fromString=None, toString=None),
        )
    )
    listed = classes(doc)
    assert not usable(listed, "changelog.items[field=Owner].from/to")
    assert usable(listed, "changelog.items[field=Label].from/to")
    assert "changelog.items[field=Label].fromString/toString" not in listed


def test_the_time_entry_pair_and_history_members_are_always_listed() -> None:
    listed = classes(issue({"a": 1}))
    assert listed[TIME_ENTRY_PAIR] == (True, "always listed")
    for member in HISTORY_MEMBERS:
        assert listed[member] == (True, "always listed")


def test_the_time_entry_pair_stays_usable_with_long_values() -> None:
    doc = with_changelog(history(item("timespent", **{"from": "1" * 200, "to": "2"})))
    assert classes(doc)[TIME_ENTRY_PAIR] == (True, "always listed")


@pytest.mark.parametrize("name", ["Comment", "description", "SUMMARY", "Environment"])
def test_prose_items_are_free_text_on_both_pairs(name: str) -> None:
    doc = with_changelog(history(item(name, **{"from": "1", "to": "2"}, toString="short")))
    listed = classes(doc)
    assert listed[pair_path(name, ("from", "to"))] == (False, "prose")
    assert listed[pair_path(name, ("fromString", "toString"))] == (False, "prose")


def test_display_names_and_comments_are_free_text_whatever_the_rules_say() -> None:
    doc = issue(
        {
            "assignee": {"displayName": "Ann", "key": "ann"},
            "comments": [{"id": "1", "created": "2020-01-01"}],
            "comment": {"total": 1, "comments": [{"id": "2"}]},
            "summary": "Short",
            "description": "Short",
            "environment": "Short",
        }
    )
    listed = classes(doc)
    for path in (
        "fields.assignee.displayName",
        "fields.comments[].id",
        "fields.comments[].created",
        "fields.comment.total",
        "fields.comment.comments[].id",
        "fields.summary",
        "fields.description",
        "fields.environment",
    ):
        assert listed[path] == (False, "prose"), path
    assert usable(listed, "fields.assignee.key")


def test_every_walked_non_empty_path_is_in_exactly_one_list() -> None:
    docs = [
        issue({"n": 1, "s": "x", "o": {"name": "y", "deep": [[True]]}, "t": "a\nb"}),
        with_changelog(history(item("Status", **{"from": "1"}, toString="Done"))),
    ]
    summaries = summarize(pair for doc in docs for pair in walked_values(doc))
    found = field_classes(summaries)
    usable_paths = [i.path for i in found.usable]
    free_paths = [i.path for i in found.free_text]
    assert len(set(usable_paths)) == len(usable_paths)
    assert not set(usable_paths) & set(free_paths)
    assert set(usable_paths) | set(free_paths) == set(summaries) | set(ALWAYS_USABLE)


def test_a_path_with_a_backtick_stops() -> None:
    doc = with_changelog(history(item("odd`name", **{"from": "1"})))
    with pytest.raises(EvalError):
        classes(doc)


def test_an_item_without_a_field_name_stops() -> None:
    with pytest.raises(EvalError, match="A-1"):
        classes(with_changelog(history({"from": "1", "to": "2"})))
