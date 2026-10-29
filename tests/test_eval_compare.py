"""Unit tests for the comparison rule (spec 0005, AC-37 to AC-40, AC-44).

Known answers are written in the `answers.json` form; store answers go through
`store_answer`, the converter both stores share. The values are synthetic.
"""

from decimal import Decimal
from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.compare import Verdict, compare, number_match, store_answer, to_number


def known(shape: str, value: Any, sources: list[dict[str, list[str]]] | None = None) -> dict:
    return {"shape": shape, "sources": sources or [], "value": value}


# AC-44: the converter


def test_true_is_not_one() -> None:
    assert to_number(True) is True
    verdict = compare(known("number", {"int": 1}), store_answer("number", True))
    assert not verdict.answer_match


@pytest.mark.parametrize("raw", [5, 5.0, Decimal("5"), Decimal("5.000")])
def test_whole_numbers_of_every_type_match_five(raw: object) -> None:
    assert to_number(raw) == {"int": 5}
    assert compare(known("number", {"int": 5}), store_answer("number", raw)).answer_match


def test_a_float_keeps_its_exact_binary_value() -> None:
    assert to_number(0.1) == {"num": 3602879701896397, "den": 36028797018963968}
    assert to_number(Decimal("2.5")) == {"num": 5, "den": 2}
    assert not number_match({"int": 0}, to_number(0.1))
    assert number_match({"num": 1, "den": 10}, to_number(0.1))


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), Decimal("NaN"), "5", [5]])
def test_a_value_with_no_exact_number_stops(raw: object) -> None:
    with pytest.raises(EvalError):
        to_number(raw)


def test_none_stays_none() -> None:
    assert to_number(None) is None


# AC-38: numbers


def test_a_fraction_matches_within_one_part_per_billion() -> None:
    five_halves = known("number", {"num": 5, "den": 2})
    assert compare(five_halves, store_answer("number", 2.5000000001)).answer_match
    assert not compare(five_halves, store_answer("number", 2.5001)).answer_match


def test_an_int_needs_its_exact_value() -> None:
    assert not compare(known("number", {"int": 5}), store_answer("number", 5.0000001)).answer_match


def test_an_unreduced_fraction_matches_its_value() -> None:
    assert compare(known("number", {"num": 10, "den": 4}), store_answer("number", 2.5)).answer_match


@pytest.mark.parametrize(("store", "matches"), [(None, True), ([], True), ({}, True), (0, False)])
def test_a_zero_denominator_matches_only_null_or_empty(store: object, matches: bool) -> None:
    raw = to_number(store) if isinstance(store, int) else store
    assert number_match({"num": 0, "den": 0}, raw) is matches


def test_a_zero_denominator_inside_a_map_matches_null() -> None:
    known_map = known("map", [{"id": "project:A", "value": {"num": 0, "den": 0}}])
    assert compare(known_map, store_answer("map", {"project:A": None})).answer_match
    assert not compare(known_map, store_answer("map", {"project:A": 0})).answer_match


# AC-39: shapes


def test_map_and_set_ignore_order() -> None:
    known_map = known(
        "map", [{"id": "person:a", "value": {"int": 1}}, {"id": "person:b", "value": {"int": 2}}]
    )
    assert compare(known_map, store_answer("map", [("person:b", 2), ("person:a", 1)])).answer_match
    assert not compare(known_map, store_answer("map", [("person:a", 1)])).answer_match
    assert not compare(
        known_map, store_answer("map", [("person:a", 1), ("person:b", True)])
    ).answer_match
    known_set = known("set", ["issue:A-1", "issue:A-2"])
    assert compare(known_set, store_answer("set", ["issue:A-2", "issue:A-1"])).answer_match
    assert not compare(known_set, store_answer("set", ["issue:A-2"])).answer_match


def test_boolean_must_be_equal_and_a_bool() -> None:
    assert compare(known("boolean", False), store_answer("boolean", False)).answer_match
    assert not compare(known("boolean", True), store_answer("boolean", False)).answer_match
    assert not compare(known("boolean", True), store_answer("boolean", 1)).answer_match


def test_a_different_shape_never_matches() -> None:
    assert not compare(known("set", []), store_answer("path", [])).answer_match


RANKING = known(
    "ranking",
    [
        {"value": {"num": 10, "den": 3}, "ids": ["person:a", "person:b"]},
        {"value": {"int": 2}, "ids": ["person:c"]},
    ],
)


def test_a_ranking_with_float_noise_between_tied_items_matches() -> None:
    store = store_answer(
        "ranking",
        [("person:b", 3.3333333333333335), ("person:a", 3.333333333333333), ("person:c", 2)],
    )
    assert compare(RANKING, store).answer_match


def test_a_ranking_with_an_extra_or_missing_item_fails() -> None:
    extra = [("person:a", 10 / 3), ("person:b", 10 / 3), ("person:c", 2), ("person:d", 1)]
    missing = [("person:a", 10 / 3), ("person:c", 2)]
    assert not compare(RANKING, store_answer("ranking", extra)).answer_match
    assert not compare(RANKING, store_answer("ranking", missing)).answer_match


def test_an_item_of_a_later_group_placed_first_fails() -> None:
    store = store_answer("ranking", [("person:c", 2), ("person:a", 10 / 3), ("person:b", 10 / 3)])
    assert not compare(RANKING, store).answer_match


def test_a_ranking_value_off_its_group_fails() -> None:
    store = store_answer("ranking", [("person:a", 10 / 3), ("person:b", 3.4), ("person:c", 2)])
    assert not compare(RANKING, store).answer_match


def test_the_store_grouping_is_ignored() -> None:
    store = {
        "shape": "ranking",
        "sources": [],
        "value": [
            {"value": {"num": 10, "den": 3}, "ids": ["person:a"]},
            {"value": {"num": 10, "den": 3}, "ids": ["person:b"]},
            {"value": {"int": 2}, "ids": ["person:c"]},
        ],
    }
    assert compare(RANKING, store).answer_match


def path_item(ids: list[str], steps: list[dict[str, list[str]]]) -> dict[str, Any]:
    return {"path": ids, "sources": steps}


PATHS = known(
    "path",
    [
        path_item(["issue:A-1", "issue:B-1"], [{"issue_link": ["10"]}]),
        path_item(
            ["issue:A-1", "issue:C-1", "issue:B-1"], [{"issue_link": ["11"]}, {"parent": ["B-1"]}]
        ),
    ],
)


def test_paths_reordered_match() -> None:
    store = store_answer(
        "path",
        [
            (
                ["issue:A-1", "issue:C-1", "issue:B-1"],
                [{"issue_link": ["11"]}, {"parent": ["B-1"]}],
            ),
            (["issue:A-1", "issue:B-1"], [{"issue_link": ["10"]}]),
        ],
    )
    assert compare(PATHS, store) == Verdict(True, True, True)


def test_steps_reordered_inside_a_path_do_not_match() -> None:
    store = store_answer(
        "path",
        [
            (["issue:A-1", "issue:B-1"], [{"issue_link": ["10"]}]),
            (
                ["issue:B-1", "issue:C-1", "issue:A-1"],
                [{"issue_link": ["11"]}, {"parent": ["B-1"]}],
            ),
        ],
    )
    assert not compare(PATHS, store).answer_match


def test_a_path_with_its_source_steps_swapped_fails_source_match() -> None:
    store = store_answer(
        "path",
        [
            (["issue:A-1", "issue:B-1"], [{"issue_link": ["10"]}]),
            (
                ["issue:A-1", "issue:C-1", "issue:B-1"],
                [{"parent": ["B-1"]}, {"issue_link": ["11"]}],
            ),
        ],
    )
    assert compare(PATHS, store) == Verdict(True, False, False)


# AC-40: sources


SOURCED = known("number", {"int": 120}, [{"time_entry": ["1", "2"]}])


def test_an_extra_source_id_fails_source_match_while_the_answer_holds() -> None:
    verdict = compare(SOURCED, store_answer("number", 120, [{"time_entry": ["1", "2", "3"]}]))
    assert verdict == Verdict(answer_match=True, source_match=False, correct=False)


def test_a_missing_source_id_or_step_fails() -> None:
    assert not compare(SOURCED, store_answer("number", 120, [{"time_entry": ["1"]}])).source_match
    assert not compare(SOURCED, store_answer("number", 120)).source_match
    two_steps = [{"time_entry": ["1", "2"]}, {"issue": ["A-1"]}]
    assert not compare(SOURCED, store_answer("number", 120, two_steps)).source_match


def test_source_ids_are_compared_as_sets_in_any_order() -> None:
    verdict = compare(SOURCED, store_answer("number", 120, [{"time_entry": [2, 1]}]))
    assert verdict == Verdict(True, True, True)


def test_a_wrong_answer_with_right_sources_is_not_correct() -> None:
    verdict = compare(SOURCED, store_answer("number", 119, [{"time_entry": ["1", "2"]}]))
    assert verdict == Verdict(answer_match=False, source_match=True, correct=False)


def test_a_no_answer_has_an_empty_step_list() -> None:
    assert compare(known("boolean", False), store_answer("boolean", False, [])).source_match
