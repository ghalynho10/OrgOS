"""Unit tests for the pinned rules of spike 0001 (spec 0004).

`spikes/` is not a package, so `rules.py` is loaded by file path. The
constants are pinned to the spec's rule table: an edit to `rules.py` fails
here before any script can run on it (AC-1).
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes" / "0001-time-data-coverage"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("spike0001_rules", SPIKE / "rules.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rules = _load()


def history(*items: dict[str, Any], author: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
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


def entry(amount: int, key: str | None = "u1", account_id: str | None = None) -> Any:
    return rules.TimeEntry(
        issue_key="P-1",
        amount=amount,
        author=rules.Author(account_id=account_id, key=key, name=key),
        created=None,
        worklog_id=None,
        timespent_items=1,
    )


def issue(
    key: str, entries: list[Any], original: int = 0, remaining: int = 0, timespent: int = 0
) -> Any:
    return rules.IssueFacts(
        key=key,
        project=key.split("-")[0],
        timespent=timespent,
        original_estimate=original,
        remaining_estimate=remaining,
        entries=tuple(entries),
    )


# AC-1: the constants equal the rule table


def test_constants_pinned_to_the_rule_table() -> None:
    assert (rules.N, rules.X_PERCENT, rules.P, rules.K) == (30, 20, 3, 2)
    assert rules.ESTIMATE_FIELD == "timeoriginalestimate"
    assert rules.OTHER_ESTIMATE_FIELD == "timeestimate"
    assert (rules.BOT_MIN_POSITIVE, rules.BOT_SHARE_NUMERATOR, rules.BOT_SHARE_DENOMINATOR) == (
        20,
        9,
        10,
    )
    assert rules.ROUND_HOUR_SECONDS == 3600
    assert (rules.GROUP_TARGET_MIN, rules.GROUP_MAX, rules.GROUP_SHORT_MIN) == (5, 10, 3)
    assert (rules.K_FALLBACK, rules.N_RELAXED) == (1, 20)
    assert (rules.PERSON_ID_ACCOUNT_ID, rules.PERSON_ID_KEY) == ("accountId", "key")
    assert (rules.PERSON_ID_MIN_PERCENT, rules.PERSON_ID_SHARE_FINDING_PERCENT) == (50, 95)
    assert (rules.ENTRY_FIELD, rules.ENTRY_FIELDTYPE, rules.WORKLOG_FIELD) == (
        "timespent",
        "jira",
        "WorklogId",
    )
    assert rules.NONE_PROJECT == "(none)"
    assert rules.SEED == 20261028
    assert (
        rules.QUOTA_TIMED,
        rules.QUOTA_NO_TIME,
        rules.QUOTA_FLAGGED,
        rules.QUOTA_NEGATIVE,
        rules.QUOTA_LINKS,
    ) == (100, 60, 30, 30, 40)
    assert rules.PLACEHOLDER == "[prose removed for fixtures]"
    assert rules.FIXTURE_MAX_BYTES == 10_000_000


# AC-4: the only client in the spike folder is the reader login


def test_spike_opens_no_other_connection() -> None:
    sources = {path.name: path.read_text() for path in SPIKE.glob("*.py")}
    assert "rules.py" in sources
    for name, text in sources.items():
        for call in re.findall(r"MongoClient\(([^,)]*)", text):
            assert call.strip() == "Settings().mongo_reader_uri", name
        assert "mongodb://" not in text, name
        for banned in ("requests", "httpx", "urllib.request", "socket", "anthropic", "pydantic_ai"):
            assert not re.search(rf"^\s*(import|from)\s+{re.escape(banned)}\b", text, re.M), name


def test_rules_module_does_no_io() -> None:
    text = (SPIKE / "rules.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+(pymongo|orgos|subprocess|os)\b", text, re.M)
    assert "open(" not in text


# AC-7: time entries from synthetic histories


def test_empty_from_counts_as_zero() -> None:
    got = rules.entry_from_history("P-1", history(spent(None, "3600")))
    assert got is not None and got.amount == 3600


def test_both_empty_count_as_zero() -> None:
    got = rules.entry_from_history("P-1", history(spent("", None, "", None)))
    assert got is not None and got.amount == 0


def test_string_fallback_only_when_raw_is_empty() -> None:
    got = rules.entry_from_history("P-1", history(spent("", None, "600", "1800")))
    assert got is not None and got.amount == 1200
    raw_wins = rules.entry_from_history("P-1", history(spent("100", "200", "999", "999")))
    assert raw_wins is not None and raw_wins.amount == 100


def test_non_integer_value_stops_and_names_the_issue() -> None:
    for bad in ("1h", "3600.0", 3600.0, True):
        with pytest.raises(rules.RuleError, match="P-9"):
            rules.entry_from_history("P-9", history(spent(None, bad)))


def test_integer_typed_values_are_accepted() -> None:
    got = rules.entry_from_history("P-1", history(spent(60, 120)))
    assert got is not None and got.amount == 60


def test_two_timespent_items_are_summed_into_one_entry() -> None:
    got = rules.entry_from_history("P-1", history(spent("0", "60"), spent("60", "180")))
    assert got is not None
    assert (got.amount, got.timespent_items) == (180, 2)


def test_negative_amount_is_a_decrease() -> None:
    got = rules.entry_from_history("P-1", history(spent("7200", "3600")))
    assert got is not None and got.amount == -3600


def test_custom_time_spent_field_is_ignored() -> None:
    custom = {"field": "Time Spent", "fieldtype": "custom", "from": None, "to": "3600"}
    assert rules.entry_from_history("P-1", history(custom)) is None
    wrong_type = {**spent(None, "60"), "fieldtype": "custom"}
    assert rules.entry_from_history("P-1", history(wrong_type)) is None


def test_worklog_id_read_from_either_side_of_the_sibling_item() -> None:
    from_side = {"field": "WorklogId", "from": "111", "to": None}
    to_side = {"field": "WorklogId", "from": None, "to": "222"}
    got_from = rules.entry_from_history("P-1", history(spent(None, "60"), from_side))
    got_to = rules.entry_from_history("P-1", history(to_side, spent(None, "60")))
    assert got_from is not None and got_from.worklog_id == "111"
    assert got_to is not None and got_to.worklog_id == "222"
    alone = rules.entry_from_history("P-1", history(spent(None, "60")))
    assert alone is not None and alone.worklog_id is None


def test_author_and_time_come_from_the_history_entry() -> None:
    author = {"accountId": "acc-1", "key": "k1", "name": "n1"}
    got = rules.entry_from_history("P-1", history(spent(None, "60"), author=author))
    assert got is not None
    assert got.author == rules.Author(account_id="acc-1", key="k1", name="n1")
    assert got.created == "2020-01-01T00:00:00.000+0000"


def test_entries_from_issue_and_project_key() -> None:
    doc = {
        "key": "SL-1",
        "fields": {"project": {"key": "SL"}, "timespent": 60, "timeoriginalestimate": None},
        "changelog": {"histories": [history(spent(None, "60")), history({"field": "status"})]},
    }
    facts = rules.issue_facts(doc)
    assert facts.project == "SL"
    assert [e.amount for e in facts.entries] == [60]
    assert (facts.timespent, facts.original_estimate) == (60, 0)
    assert rules.project_key({"fields": {}}) == "(none)"
    assert rules.entries_from_issue({"key": "X-1"}) == ()


# AC-8: bots


def test_nineteen_positive_entries_never_flag() -> None:
    assert not rules.check_account("a", [3600] * 19).flagged


def test_ninety_percent_flags_and_eighty_nine_does_not() -> None:
    assert rules.check_account("a", [60] * 18 + [120, 180]).flagged  # 18 of 20 = 90 percent
    at_89 = [60] * 89 + list(range(1000, 1011))  # 89 of 100
    assert not rules.check_account("a", at_89).flagged


def test_zero_and_negative_entries_do_not_count_as_positive() -> None:
    check = rules.check_account("a", [60] * 19 + [0, -60, -60])
    assert (check.entries, check.positive, check.flagged) == (22, 19, False)


def test_tie_for_top_amount_takes_the_smaller() -> None:
    check = rules.check_account("a", [120] * 3 + [60] * 3)
    assert (check.top_amount, check.top_count) == (60, 3)


def test_round_hour_marking() -> None:
    assert rules.check_account("a", [7200] * 20).round_hour
    assert not rules.check_account("a", [1800] * 20).round_hour


def test_flagged_account_excluded_with_all_its_entries() -> None:
    bot = [entry(60, key="bot")] * 20 + [entry(0, key="bot"), entry(-60, key="bot")]
    human = [entry(600, key="h")]
    flagged = rules.flagged_ids(rules.check_accounts(bot + human, "key"))
    assert flagged == frozenset({"bot"})
    facts = issue("P-1", [*bot, *human], original=1)
    assert rules.net_seconds(facts, flagged, "key") == 600


def test_flag_in_one_collection_does_not_touch_another() -> None:
    collection_a = [entry(60, key="shared")] * 20
    collection_b = [entry(60, key="shared"), entry(120, key="shared")]
    flags_a = rules.flagged_ids(rules.check_accounts(collection_a, "key"))
    flags_b = rules.flagged_ids(rules.check_accounts(collection_b, "key"))
    assert flags_a == frozenset({"shared"})
    assert flags_b == frozenset()


def test_entries_without_person_id_are_never_flagged() -> None:
    nameless = [entry(60, key=None)] * 25
    assert rules.check_accounts(nameless, "key") == ()


def test_lift_round_hour_is_a_diagnostic_view() -> None:
    checks = rules.check_accounts([entry(3600, key="r")] * 20 + [entry(60, key="s")] * 20, "key")
    assert rules.flagged_ids(checks) == frozenset({"r", "s"})
    assert rules.flagged_ids(checks, lift_round_hour=True) == frozenset({"s"})


def test_bot_time_share() -> None:
    entries = [entry(60, key="bot"), entry(-60, key="bot"), entry(140, key="h")]
    assert rules.bot_time(entries, frozenset({"bot"}), "key") == (60, 200)


# AC-9: the fills rule


def _measure(qualifying: int, total: int, people: int, project: str = "P") -> Any:
    return rules.ProjectMeasure(
        project, total, qualifying, 0, frozenset(f"u{i}" for i in range(people))
    )


def test_twenty_nine_fails_n_and_thirty_passes() -> None:
    base = rules.rule_set(0)
    assert rules.failing_parts(_measure(29, 29, 3), base) == ("N",)
    assert rules.failing_parts(_measure(30, 30, 3), base) == ()


def test_share_exactly_twenty_percent_passes_and_just_under_fails() -> None:
    base = rules.rule_set(0)
    assert rules.failing_parts(_measure(30, 150, 3), base) == ()
    assert rules.failing_parts(_measure(30, 151, 3), base) == ("X",)


def test_two_people_fail_and_three_pass() -> None:
    base = rules.rule_set(0)
    assert rules.failing_parts(_measure(30, 30, 2), base) == ("P",)
    assert rules.failing_parts(_measure(30, 30, 3), base) == ()


def test_each_failing_part_is_named() -> None:
    assert rules.failing_parts(_measure(1, 100, 1), rules.rule_set(0)) == ("N", "X", "P")
    assert rules.failing_parts(_measure(1, 100, 1), rules.rule_set(1)) == ("N", "P")


def test_none_is_never_a_candidate() -> None:
    measure = _measure(100, 100, 10, project="(none)")
    assert rules.failing_parts(measure, rules.rule_set(0)) == ("not a project",)
    assert not rules.stage1_survives("(none)", 1000, 30)
    assert rules.stage1_survives("P", 30, 30)
    assert not rules.stage1_survives("P", 29, 30)


def test_entry_without_person_id_counts_in_hours_but_not_as_person() -> None:
    issues = [issue("P-1", [entry(600, key=None)], original=60)]
    measure = rules.measure_project("P", 1, issues, frozenset(), "key")
    assert measure.qualifying == 1
    assert measure.people == frozenset()


def test_measure_counts_both_estimate_fields_and_net_time() -> None:
    issues = [
        issue("P-1", [entry(60, key="a")], original=10, remaining=0),
        issue("P-2", [entry(60, key="b")], original=0, remaining=10),
        issue("P-3", [entry(60, key="c"), entry(-60, key="c")], original=10, remaining=10),
        issue("P-4", [entry(60, key="bot")], original=10, remaining=10),
    ]
    measure = rules.measure_project("P", 4, issues, frozenset({"bot"}), "key")
    assert (measure.qualifying, measure.qualifying_remaining) == (1, 1)
    assert measure.people == frozenset({"a", "b", "c"})


# AC-10: entry match


def test_match_status() -> None:
    none = frozenset[str]()
    assert rules.match_status(issue("P-1", [entry(60)], timespent=60), none, "key") == "match"
    assert rules.match_status(issue("P-1", [entry(60)], timespent=90), none, "key") == "mismatch"
    assert rules.match_status(issue("P-1", [], timespent=90), none, "key") == "mismatch"
    assert rules.match_status(issue("P-1", [], timespent=0), none, "key") is None
    flagged = frozenset({"u1"})
    assert rules.match_status(issue("P-1", [entry(60)], timespent=60), flagged, "key") is None


def test_field_number_reads_null_and_non_numbers_as_zero() -> None:
    assert [rules.field_number(v) for v in (None, "60", True, 60, 1.5)] == [0, 0, 0, 60, 1.5]


# AC-11: person id


def test_forty_nine_percent_chooses_key_and_fifty_chooses_account_id() -> None:
    assert rules.choose_person_id_field(49, 100) == "key"
    assert rules.choose_person_id_field(50, 100) == "accountId"
    assert rules.choose_person_id_field(0, 0) == "key"


def test_person_id_uses_the_chosen_field() -> None:
    author = rules.Author(account_id="acc", key="k", name="n")
    assert rules.person_id(author, "accountId") == "acc"
    assert rules.person_id(author, "key") == "k"


def test_share_under_ninety_five_percent_is_a_finding() -> None:
    assert rules.person_share_is_finding(94, 100)
    assert not rules.person_share_is_finding(95, 100)


# AC-12: linking and ranking


def _people(**projects: str) -> dict[str, frozenset[str]]:
    return {name: frozenset(members.split()) for name, members in projects.items()}


def test_path_a_to_b_to_c_is_one_group() -> None:
    people = _people(A="x y", B="x y z w", C="z w")
    assert rules.groups(people, 2) == (("A", "B", "C"),)


def test_one_person_bridge_links_at_k1_not_k2() -> None:
    people = _people(A="x y", B="y z")
    assert rules.groups(people, 2) == (("A",), ("B",))
    assert rules.groups(people, 1) == (("A", "B"),)


def test_equal_groups_tie_by_qualifying_then_smallest_key() -> None:
    found = (("A", "B"), ("C", "D"))
    assert rules.largest_group(found, {"A": 1, "B": 1, "C": 5, "D": 1}) == ("C", "D")
    assert rules.largest_group(found, {"A": 3, "B": 3, "C": 3, "D": 3}) == ("A", "B")
    assert rules.largest_group((("Z",), ("A", "B")), {"Z": 100, "A": 1, "B": 1}) == ("A", "B")
    assert rules.largest_group((), {}) == ()


def test_collection_ties_by_name_in_code_point_order() -> None:
    scores = [
        rules.CollectionScore("b", ("P",), 10),
        rules.CollectionScore("B", ("P",), 10),
        rules.CollectionScore("A", ("P", "Q"), 1),
        rules.CollectionScore("C", ("P",), 20),
    ]
    assert [s.collection for s in rules.rank_collections(scores)] == ["A", "C", "B", "b"]


# AC-13: the greedy pick


def test_greedy_pick_of_twelve_gives_ten_in_order() -> None:
    names = [f"P{i:02d}" for i in range(12)]
    people = {name: frozenset({"x", "y"}) for name in names}
    qualifying = {name: 100 - i for i, name in enumerate(names)}
    picked = rules.greedy_pick(names, qualifying, people, 2)
    assert picked == tuple(names[:10])


def test_greedy_pick_tie_goes_to_smaller_key_and_follows_links() -> None:
    people = _people(A="x y", B="x y", C="z w", D="x y z w")
    qualifying = {"A": 5, "B": 5, "C": 50, "D": 1}
    # C first (most), then only D is linked to C, then A before B on the tie.
    assert rules.greedy_pick(["A", "B", "C", "D"], qualifying, people, 2) == ("C", "D", "A", "B")


def test_greedy_pick_of_empty_group() -> None:
    assert rules.greedy_pick([], {}, {}, 2) == ()


# AC-14: the ladder


def test_rule_sets_are_cumulative() -> None:
    assert rules.rule_set(0) == rules.RuleSet(0, 30, 20, 3, 2)
    assert rules.rule_set(1) == rules.RuleSet(1, 30, None, 3, 2)
    assert rules.rule_set(2) == rules.RuleSet(2, 30, None, 3, 1)
    assert rules.rule_set(3) == rules.RuleSet(3, 30, None, 3, 1)
    assert rules.rule_set(0, relaxed=True) == rules.RuleSet(0, 20, 20, 3, 2)
    with pytest.raises(rules.RuleError):
        rules.rule_set(4)


@pytest.mark.parametrize(
    ("step", "size", "relaxed", "verdict"),
    [
        (0, 5, False, "accept"),
        (0, 4, False, "next"),
        (1, 7, False, "accept"),
        (1, 0, False, "next"),
        (2, 5, False, "accept"),
        (2, 4, False, "accept_short"),
        (2, 3, False, "accept_short"),
        (2, 2, False, "stop"),
        (2, 0, False, "stop"),
        (0, 4, True, "next"),
        (2, 3, True, "accept_short"),
        (2, 2, True, "no_corpus"),
    ],
)
def test_ladder_verdicts(step: int, size: int, relaxed: bool, verdict: str) -> None:
    assert rules.ladder_verdict(step, size, relaxed=relaxed) == verdict
