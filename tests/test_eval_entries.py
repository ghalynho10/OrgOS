"""Unit tests for the entry rebuild, bot flags and person field (spec 0005, AC-20, AC-21, AC-25).

The AC-7, AC-8 and AC-11 cases are the spike's inputs and expected outputs
(`tests/test_spike_rules.py`), rewritten against `rebuild_entries`,
`flag_bots` and `choose_person_field`. Each synthetic history now carries an
`id`, which an entry needs here.
"""

import datetime as dt
from itertools import count
from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.entries import (
    TimeEntry,
    choose_person_field,
    entry_authors,
    flag_bots,
    parse_utc,
    rebuild_entries,
    top_amount,
)

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


@pytest.mark.parametrize("empty", [None, ""])
def test_a_time_entry_history_with_an_empty_id_stops_and_names_the_issue(empty: object) -> None:
    doc = issue({**history(spent(None, "60")), "id": empty}, key="P-3")
    with pytest.raises(EvalError, match=r"^P-3: a time entry's changelog history has no id$"):
        rebuild_entries(doc, "key")


def test_an_int_id_is_read_as_its_string() -> None:
    assert one(issue({**history(spent(None, "60")), "id": 10900})).history_id == "10900"


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


# AC-8 (spec 0004), through flag_bots: the spike's cases


def entry(amount: int, key: str | None = "u1") -> TimeEntry:
    return TimeEntry("1", "P-1", "P", key, dt.datetime(2020, 1, 1, tzinfo=dt.UTC), amount)


def entries(amounts: list[int], key: str | None = "a") -> list[TimeEntry]:
    return [entry(amount, key) for amount in amounts]


def test_nineteen_positive_entries_never_flag() -> None:
    assert flag_bots(entries([3600] * 19)) == frozenset()


def test_ninety_percent_flags_and_eighty_nine_does_not() -> None:
    assert flag_bots(entries([60] * 18 + [120, 180])) == {"a"}  # 18 of 20 = 90 percent
    assert flag_bots(entries([60] * 89 + list(range(1000, 1011)))) == frozenset()  # 89 of 100


def test_zero_and_negative_entries_do_not_count_as_positive() -> None:
    assert flag_bots(entries([60] * 19 + [0, -60, -60])) == frozenset()


def test_tie_for_top_amount_takes_the_smaller() -> None:
    assert top_amount([120] * 3 + [60] * 3) == (60, 3)
    assert top_amount([]) == (0, 0)


def test_a_flag_covers_the_account_wherever_its_entries_are() -> None:
    bot = entries([60] * 20 + [0, -60], key="bot")
    assert flag_bots([*bot, entry(600, key="h")]) == {"bot"}


def test_entries_without_person_id_are_never_flagged() -> None:
    assert flag_bots(entries([60] * 25, key=None)) == frozenset()


# AC-11 (spec 0004), through choose_person_field


def authors(with_account_id: int, total: int) -> list[dict[str, Any] | None]:
    return [{"key": "k", "accountId": "a"}] * with_account_id + [{"key": "k", "accountId": ""}] * (
        total - with_account_id
    )


def test_forty_nine_percent_chooses_key_and_fifty_chooses_account_id() -> None:
    assert choose_person_field(authors(49, 100)) == "key"
    assert choose_person_field(authors(50, 100)) == "accountId"
    assert choose_person_field([]) == "key"


def test_an_author_missing_counts_as_lacking_the_account_id() -> None:
    assert choose_person_field([{"accountId": "a"}, None, None]) == "key"


def test_entry_authors_are_the_authors_of_time_entry_histories_only() -> None:
    doc = issue(
        history(spent(None, "60"), author={"key": "k1"}),
        history({"field": "status"}, author={"key": "k2"}),
    )
    doc["changelog"]["histories"].append({"id": "x", "items": [spent(None, "60")]})
    assert entry_authors(doc) == ({"key": "k1"}, None)
