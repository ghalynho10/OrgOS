"""Unit tests for the hours, person, boundary, path and UTC rules.

Spec 0005, AC-22 to AC-25 and AC-48. No store: synthetic entries and links.
"""

import datetime as dt

import pytest

from orgos.eval import EvalError
from orgos.eval.entries import TimeEntry
from orgos.eval.rules import (
    IssueLink,
    Parent,
    corpus_links,
    corpus_parents,
    in_interval,
    logged_seconds,
    people_who_logged,
    role_person,
    simple_paths,
    stored_links,
    undirected,
    utc_time,
)

AT = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


def entry(amount: int, key: str | None = "p", history_id: str = "1") -> TimeEntry:
    return TimeEntry(history_id, "P-1", "P", key, AT, amount)


# AC-22, AC-23: hours and people


def test_negative_amounts_never_enter_logged_hours() -> None:
    assert logged_seconds([entry(600), entry(-300), entry(0)], frozenset()) == 600


def test_bot_entries_never_enter_logged_hours_and_authorless_ones_do() -> None:
    got = logged_seconds([entry(600, "bot"), entry(60, None), entry(30)], frozenset({"bot"}))
    assert got == 90


def test_a_person_with_only_negative_entries_did_not_log_time() -> None:
    entries = [entry(-600, "neg"), entry(0, "zero"), entry(60, "pos"), entry(60, None)]
    assert people_who_logged(entries, frozenset()) == {"pos"}


def test_a_flagged_account_is_never_a_person_who_logged_time() -> None:
    assert people_who_logged([entry(60, "bot")], frozenset({"bot"})) == frozenset()


def test_an_assignee_with_no_entries_is_still_read_by_key() -> None:
    assert role_person("k1", frozenset()) == "k1"


@pytest.mark.parametrize("key", ["bot", None, ""])
def test_a_flagged_or_missing_role_key_is_no_person(key: str | None) -> None:
    assert role_person(key, frozenset({"bot"})) is None


# AC-25: UTC, half open


def test_a_late_new_year_eve_time_lands_in_the_next_year() -> None:
    moment = utc_time("2012-12-31T23:30:00.000-0500", "P-1")
    assert in_interval(
        moment, dt.datetime(2013, 1, 1, tzinfo=dt.UTC), dt.datetime(2014, 1, 1, tzinfo=dt.UTC)
    )


def test_the_end_bound_is_excluded_and_the_start_included() -> None:
    start, end = dt.datetime(2013, 1, 1, tzinfo=dt.UTC), dt.datetime(2013, 4, 1, tzinfo=dt.UTC)
    assert in_interval(start, start, end)
    assert not in_interval(end, start, end)


def test_a_datetime_value_keeps_its_zone_and_a_naive_one_stops() -> None:
    paris = dt.timezone(dt.timedelta(hours=1))
    assert utc_time(dt.datetime(2013, 1, 1, 0, 30, tzinfo=paris), "P-1") == dt.datetime(
        2012, 12, 31, 23, 30, tzinfo=dt.UTC
    )
    with pytest.raises(EvalError, match="P-2"):
        utc_time(dt.datetime(2013, 1, 1), "P-2")


@pytest.mark.parametrize("bad", ["2013-01-01T00:00:00.000", None])
def test_a_missing_offset_stops_and_names_the_issue(bad: object) -> None:
    with pytest.raises(EvalError, match="P-3"):
        utc_time(bad, "P-3")


# AC-24: the corpus boundary


def link(
    link_id: str, *, inward: str | None = None, outward: str | None = None
) -> dict[str, object]:
    raw: dict[str, object] = {"id": link_id, "type": {"name": "Blocks"}}
    if inward:
        raw["inwardIssue"] = {"key": inward}
    if outward:
        raw["outwardIssue"] = {"key": outward}
    return raw


def test_a_link_reads_the_same_from_both_of_its_issues() -> None:
    (on_a,) = stored_links("A-1", [link("7", outward="B-1")])
    (on_b,) = stored_links("B-1", [link("7", inward="A-1")])
    assert on_a == on_b == IssueLink("7", "Blocks", "A-1", "B-1")


def test_a_link_stored_on_both_issues_counts_once() -> None:
    links = [
        *stored_links("A-1", [link("7", outward="B-1")]),
        *stored_links("B-1", [link("7", inward="A-1")]),
    ]
    assert corpus_links(links, {"A-1", "B-1"}) == (IssueLink("7", "Blocks", "A-1", "B-1"),)


def test_a_link_to_an_outside_issue_is_dropped() -> None:
    links = stored_links("A-1", [link("7", outward="OUT-1"), link("8", inward="B-1")])
    assert [kept.link_id for kept in corpus_links(links, {"A-1", "B-1"})] == ["8"]


@pytest.mark.parametrize(
    "raw", [{"outwardIssue": {"key": "B-1"}}, link("7", inward="X", outward="Y")]
)
def test_a_link_without_an_id_or_naming_both_ends_stops(raw: dict[str, object]) -> None:
    with pytest.raises(EvalError, match="A-1"):
        stored_links("A-1", [raw])


def test_a_link_naming_no_other_issue_is_left_out() -> None:
    assert stored_links("A-1", [link("7")]) == ()


def test_a_parent_outside_the_corpus_is_dropped() -> None:
    parents = [Parent("A-2", "A-1"), Parent("A-3", "OUT-1")]
    assert corpus_parents(parents, {"A-1", "A-2", "A-3"}) == (Parent("A-2", "A-1"),)


# AC-48: simple paths


def test_link_direction_is_ignored() -> None:
    graph = undirected([("A", "B")])
    assert simple_paths(graph, "B", "A", 1) == (("B", "A"),)


def test_a_cycle_never_repeats_an_entity() -> None:
    graph = undirected([("A", "B"), ("B", "C"), ("C", "A"), ("C", "D")])
    paths = simple_paths(graph, "A", "D", 10)
    assert paths == (("A", "B", "C", "D"), ("A", "C", "D"))
    assert all(len(set(path)) == len(path) for path in paths)


def test_a_path_longer_than_max_hops_is_excluded() -> None:
    graph = undirected([("A", "B"), ("B", "C"), ("A", "C")])
    assert simple_paths(graph, "A", "C", 1) == (("A", "C"),)
    assert simple_paths(graph, "A", "C", 2) == (("A", "B", "C"), ("A", "C"))
