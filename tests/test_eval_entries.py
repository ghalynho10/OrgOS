"""Unit tests for the entry rebuild of the known answer code (spec 0005, AC-20, AC-25).

The AC-7 cases are the spike's inputs and expected outputs
(`tests/test_spike_rules.py`), rewritten against `rebuild_entries`. Each
synthetic history now carries an `id`, which an entry needs here.
"""

import datetime as dt
from itertools import count
from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.entries import TimeEntry, parse_utc, rebuild_entries

_ids = count(1)


def history(*items: dict[str, Any], author: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": str(next(_ids)),
        "author": author if author is not None else {"key": "u1", "name": "u1"},
        "created": "2020-01-01T00:00:00.000+0000",
        "items": list(items),
    }


def spent(frm: Any, to: Any, frm_s: Any = None, to_s: Any = None) -> dict[str, Any]:
    return {
        "field": "timespent",
        "fieldtype": "jira",
        "from": frm,
        "fromString": frm_s,
        "to": to,
        "toString": to_s,
    }


def issue(*histories: dict[str, Any], key: str = "P-1") -> dict[str, Any]:
    return {
        "key": key,
        "fields": {"project": {"key": key.split("-")[0]}},
        "changelog": {"histories": list(histories)},
    }


def one(doc: dict[str, Any], person_field: str = "key") -> TimeEntry:
    (entry,) = rebuild_entries(doc, person_field)
    return entry


# AC-7 (spec 0004), through AC-20


def test_empty_from_counts_as_zero() -> None:
    assert one(issue(history(spent(None, "3600")))).amount == 3600


def test_both_empty_count_as_zero() -> None:
    assert one(issue(history(spent("", None, "", None)))).amount == 0


def test_string_fallback_only_when_raw_is_empty() -> None:
    assert one(issue(history(spent("", None, "600", "1800")))).amount == 1200
    assert one(issue(history(spent("100", "200", "999", "999")))).amount == 100


@pytest.mark.parametrize("bad", ["1h", "3600.0", 3600.0, True])
def test_non_integer_value_stops_and_names_the_issue(bad: Any) -> None:
    with pytest.raises(EvalError, match="P-9"):
        rebuild_entries(issue(history(spent(None, bad)), key="P-9"), "key")


def test_integer_typed_values_are_accepted() -> None:
    assert one(issue(history(spent(60, 120)))).amount == 60


def test_two_timespent_items_are_summed_into_one_entry() -> None:
    assert one(issue(history(spent("0", "60"), spent("60", "180")))).amount == 180


def test_negative_amount_is_a_decrease() -> None:
    assert one(issue(history(spent("7200", "3600")))).amount == -3600


def test_custom_time_spent_field_is_ignored() -> None:
    custom = {"field": "Time Spent", "fieldtype": "custom", "from": None, "to": "3600"}
    assert rebuild_entries(issue(history(custom)), "key") == ()
    wrong_type = {**spent(None, "60"), "fieldtype": "custom"}
    assert rebuild_entries(issue(history(wrong_type)), "key") == ()


def test_author_and_time_come_from_the_history_entry() -> None:
    author = {"accountId": "acc-1", "key": "k1", "name": "n1"}
    doc = issue(history(spent(None, "60"), author=author))
    assert one(doc, "key").author_key == "k1"
    assert one(doc, "accountId").author_key == "acc-1"
    assert one(doc).created_utc == dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


def test_entries_carry_issue_project_and_history_id() -> None:
    doc = issue(history(spent(None, "60")), history({"field": "status"}), key="SL-1")
    entry = one(doc)
    assert (entry.issue_key, entry.project_key) == ("SL-1", "SL")
    assert entry.history_id == doc["changelog"]["histories"][0]["id"]
    assert rebuild_entries({"key": "X-1"}, "key") == ()
    assert one({**doc, "fields": {}}).project_key is None


# AC-20: the history id and the author key


def test_a_time_entry_history_with_no_id_stops_and_names_the_issue() -> None:
    no_id = {k: v for k, v in history(spent(None, "60")).items() if k != "id"}
    with pytest.raises(EvalError, match="P-3"):
        rebuild_entries(issue(no_id, key="P-3"), "key")


def test_a_history_with_no_id_and_no_entry_is_not_an_entry() -> None:
    no_id = {k: v for k, v in history({"field": "status"}).items() if k != "id"}
    assert rebuild_entries(issue(no_id), "key") == ()


@pytest.mark.parametrize("author", [{}, {"name": "n"}, {"key": ""}, {"key": None}])
def test_an_entry_with_no_author_key_counts_in_hours_but_is_no_person(
    author: dict[str, Any],
) -> None:
    entry = one(issue(history(spent(None, "60"), author=author)))
    assert (entry.amount, entry.author_key) == (60, None)


# AC-25: times in UTC


def test_a_late_new_year_eve_time_lands_in_the_next_year() -> None:
    got = parse_utc("2020-12-31T23:30:00.000-0500", "P-1")
    assert got == dt.datetime(2021, 1, 1, 4, 30, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    "bad", ["2020-12-31T23:30:00.000", "2020-12-31T23:30:00.000Z", "", None, 1609459200]
)
def test_a_missing_or_unreadable_offset_stops_and_names_the_issue(bad: Any) -> None:
    with pytest.raises(EvalError, match="P-4"):
        parse_utc(bad, "P-4")
    doc = issue(history(spent(None, "60")), key="P-4")
    doc["changelog"]["histories"][0]["created"] = bad
    with pytest.raises(EvalError, match="P-4"):
        rebuild_entries(doc, "key")
