"""Unit tests for the answer writer and the amendment triggers (spec 0005, AC-29 to AC-33).

Pure: answers built in memory, rendered to bytes, no store and no file.
"""

import json

import pytest

from orgos.eval import EvalError
from orgos.eval.writer import (
    answer_triggers,
    boolean_answer,
    count,
    map_answer,
    number_answer,
    path_answer,
    ranking_answer,
    ratio,
    render,
    set_answer,
    step,
)

SHA = "0" * 64


def test_shuffled_input_gives_identical_bytes() -> None:
    keys = ["B-10", "B-9", "A-1", "a-1", "Z-1"]
    first = map_answer(
        "project", {"X": count(1), "B": count(2)}, (step({"issue": keys, "person": ["u2", "u1"]}),)
    )
    second = map_answer(
        "project",
        {"B": count(2), "X": count(1)},
        (step({"person": ["u1", "u2"], "issue": list(reversed(keys))}),),
    )
    assert render({"Q01": first}, SHA, None) == render({"Q01": second}, SHA, None)


def test_ids_sort_in_code_point_order_and_the_file_is_laid_out() -> None:
    answer = set_answer("issue", ["a-1", "B-9", "B-10", "Z-1"], (step({"issue": ["a-1", "B-9"]}),))
    text = render({"Q02": answer, "Q01": boolean_answer(True, ())}, SHA, None).decode()
    assert text.endswith("}\n") and not text.endswith("\n\n")
    parsed = json.loads(text)
    assert parsed["answers"]["Q02"]["value"] == [
        "issue:B-10",
        "issue:B-9",
        "issue:Z-1",
        "issue:a-1",
    ]
    assert parsed["answers"]["Q02"]["sources"] == [{"issue": ["B-9", "a-1"]}]
    assert list(parsed) == ["amendments_sha256", "answers", "questions_sha256"]
    assert list(parsed["answers"]) == ["Q01", "Q02"]
    assert '\n  "answers": {\n    "Q01": {' in text


def test_the_header_holds_each_hash_under_its_own_key() -> None:
    questions, amendments = "1" * 64, "2" * 64
    parsed = json.loads(render({"Q01": boolean_answer(True, ())}, questions, amendments))
    assert parsed["questions_sha256"] == questions
    assert parsed["amendments_sha256"] == amendments
    assert json.loads(render({}, questions, None))["amendments_sha256"] is None


def test_non_ascii_ids_stay_as_utf8_bytes() -> None:
    rendered = render({"Q01": set_answer("issue", ["É-1"], ())}, SHA, None)
    assert "issue:É-1".encode() in rendered


def test_a_repeated_source_id_stops() -> None:
    with pytest.raises(EvalError, match="more than once"):
        step({"issue": ["A-1", "A-1"]})


def test_a_repeated_set_id_stops_and_names_it() -> None:
    with pytest.raises(EvalError, match=r"^a set answer lists \['issue:A-1'\] more than once$"):
        set_answer("issue", ["A-1", "A-1"], ())


def test_a_fraction_stays_unreduced_and_a_zero_denominator_is_a_trigger() -> None:
    kept = json.loads(render({"Q03": number_answer(ratio(10, 4), ())}, SHA, None))
    assert kept["answers"]["Q03"]["value"] == {"num": 10, "den": 4}
    zero = number_answer(ratio(0, 0), ())
    assert json.loads(render({"Q03": zero}, SHA, None))["answers"]["Q03"]["value"] == {
        "num": 0,
        "den": 0,
    }
    assert answer_triggers("Q03", zero, None) == (
        "Q03 trigger empty_answer: 1 zero denominator(s)",
    )
    assert answer_triggers("Q03", number_answer(count(0), ()), None) == ()


def test_each_zero_denominator_in_a_map_is_counted_in_its_trigger() -> None:
    answer = map_answer("project", {"A": ratio(0, 0), "B": ratio(1, 2), "C": ratio(0, 0)}, ())
    assert answer_triggers("Q03", answer, None) == (
        "Q03 trigger empty_answer: 2 zero denominator(s)",
    )


def test_a_tie_at_a_cut_of_3_keeps_all_four_tied_items_and_is_a_trigger() -> None:
    values = {
        "top": count(9),
        "a": count(5),
        "b": count(5),
        "c": count(5),
        "d": count(5),
        "e": count(1),
    }
    answer = ranking_answer("person", values, "descending", 3, ())
    groups = [(g.value.model_dump(by_alias=True), g.ids) for g in answer.value]
    assert groups == [
        ({"int": 9}, ("person:top",)),
        ({"int": 5}, ("person:a", "person:b", "person:c", "person:d")),
    ]
    assert answer_triggers("Q04", answer, 3) == (
        "Q04 trigger tie_at_cut: 4 items tie in the last group, 5 ranked for a cut of 3",
    )


def test_a_ranking_ending_at_the_cut_has_no_trigger_and_ascending_reverses() -> None:
    values = {"a": ratio(1, 2), "b": ratio(3, 4), "c": count(2)}
    answer = ranking_answer("person", values, "ascending", 2, ())
    assert [g.ids for g in answer.value] == [("person:a",), ("person:b",)]
    assert answer_triggers("Q04", answer, 2) == ()


def test_a_cut_below_one_stops() -> None:
    with pytest.raises(EvalError, match=r"^a ranking cut must be at least 1, not 0$"):
        ranking_answer("person", {"a": count(1)}, "descending", 0, ())


def test_an_item_with_a_zero_denominator_stops_instead_of_leaving_the_ranking() -> None:
    values = {"a": count(1), "b": ratio(0, 0)}
    with pytest.raises(EvalError, match=r"^person:b has a zero denominator and cannot be ranked$"):
        ranking_answer("person", values, "descending", 5, ())


def test_an_empty_ranking_is_a_trigger_and_no_tie() -> None:
    answer = ranking_answer("person", {}, "descending", 3, ())
    assert answer_triggers("Q04", answer, 3) == ("Q04 trigger empty_answer: the answer is empty",)


def test_tied_values_stored_differently_stop() -> None:
    with pytest.raises(EvalError, match="store their equal value differently"):
        ranking_answer("person", {"a": ratio(10, 4), "b": ratio(5, 2)}, "descending", 1, ())


def test_a_boolean_no_and_empty_answers_are_triggers() -> None:
    assert answer_triggers("Q05", boolean_answer(False, ()), None) == (
        "Q05 trigger empty_answer: the answer is no",
    )
    assert answer_triggers("Q05", boolean_answer(True, ()), None) == ()
    for empty in (map_answer("project", {}, ()), set_answer("issue", [], ()), path_answer([])):
        assert answer_triggers("Q06", empty, None) == (
            "Q06 trigger empty_answer: the answer is empty",
        )


def test_paths_sort_by_their_joined_ids_and_keep_their_step_order() -> None:
    steps = (step({"issue_link": ["2"]}), step({"issue_link": ["1"]}))
    answer = path_answer([(["issue:B-1", "issue:A-1"], steps), (["issue:A-1", "issue:B-1"], steps)])
    assert [item.path for item in answer.value] == [
        ("issue:A-1", "issue:B-1"),
        ("issue:B-1", "issue:A-1"),
    ]
    assert answer.value[0].sources == steps
