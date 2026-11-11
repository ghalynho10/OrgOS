"""The answer checks, `assemble` and the outcome map (spec 0012 AC-17 to AC-19, AC-21).

Results are built by hand as the tool stores them. Every assembled answer is
also passed through `store_answer`, the runner's converter, so the form is the
one the runner takes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from orgos.agent.answer import FinalAnswer, SourceRef, answer_problems, assemble
from orgos.agent.outcome import RunEnd, run_outcome
from orgos.agent.tools import StoredResult
from orgos.eval.compare import store_answer
from orgos.eval.handoff import QueryAttempt


def _answer(
    shape: str,
    result: str = "r1",
    id_kind: str | None = None,
    sources: list[dict[str, tuple[str, str]]] | None = None,
) -> FinalAnswer:
    steps = [
        {kind: SourceRef(result=handle, column=column) for kind, (handle, column) in step.items()}
        for step in (sources or [])
    ]
    return FinalAnswer(shape=shape, answer_result=result, id_kind=id_kind, sources=steps)


def _rows(columns: tuple[str, ...], *rows: tuple[Any, ...]) -> StoredResult:
    return StoredResult(columns, tuple(rows))


ISSUES = _rows(("id",), ("CLOUD-1",), ("CLOUD-2",))
COUNT = {"r1": _rows(("value",), (2,)), "r2": ISSUES}


# --- The checks (AC-17) ---


def test_a_good_number_answer_passes() -> None:
    answer = _answer("number", sources=[{"issue": ("r2", "id")}])
    assert answer_problems(answer, COUNT, "number") == ()


@pytest.mark.parametrize(
    ("answer", "results", "words"),
    [
        (_answer("number", result="r9"), COUNT, "there is no result r9"),
        (_answer("number", result="r2"), COUNT, "needs column value in r2"),
        (_answer("number"), COUNT, "asks for a set answer"),
        (_answer("total"), COUNT, "shape 'total' is not one of"),
        (_answer("map", id_kind="ticket"), {"r1": _rows(("id", "value"))}, "id_kind 'ticket'"),
        (_answer("number"), {"r1": _rows(("value",), (1,), (2,))}, "exactly 1 row in r1, it has 2"),
        (_answer("boolean"), {"r1": _rows(("value",), (1,))}, "is not true or false"),
        (_answer("set", id_kind="issue"), {"r1": _rows(("id",), (None,))}, "id of r1 holds NULL"),
        (_answer("set"), {"r1": ISSUES}, "needs id_kind, or a column kind"),
        (
            _answer("set", id_kind="issue", sources=[{"issue": ("r1", "key")}]),
            {"r1": ISSUES},
            "r1 has no column key",
        ),
        (
            _answer("set", id_kind="issue", sources=[{"epic": ("r1", "id")}]),
            {"r1": ISSUES},
            "the kind is not one of",
        ),
        (
            _answer("set", id_kind="issue", sources=[{"issue": ("r4", "id")}]),
            {"r1": ISSUES},
            "there is no result r4",
        ),
        (
            _answer("set", id_kind="issue", sources=[{"person": ("r2", "id")}]),
            {"r1": ISSUES, "r2": _rows(("id",), ("u1",), (None,))},
            "column id of r2 holds NULL",
        ),
        (_answer("set", id_kind="issue", sources=[{}]), {"r1": ISSUES}, "names no kind"),
        (
            _answer("set", id_kind="issue"),
            {"r1": _rows(("id",), (True,))},
            "must hold IDs",
        ),
    ],
)
def test_each_bad_pointer_sends_the_model_back(
    answer: FinalAnswer, results: dict[str, StoredResult], words: str
) -> None:
    expected = "set" if words.startswith("asks for") else None
    problems = answer_problems(answer, results, expected)
    assert any(words in problem for problem in problems), problems


def test_a_null_number_passes_as_missing() -> None:
    results = {"r1": _rows(("value",), (None,))}
    assert answer_problems(_answer("number"), results, "number") == ()
    assert assemble(_answer("number"), results) == (None, ())


def test_a_mixed_kind_column_replaces_id_kind() -> None:
    results = {"r1": _rows(("id", "kind"), ("u1", "person"), ("CLOUD", "project"))}
    answer = _answer("set", sources=[{"person": ("r1", "id")}])
    assert answer_problems(answer, results, "set") == ()
    value, _ = assemble(answer, results)
    assert value == ["person:u1", "project:CLOUD"]
    bad = {"r1": _rows(("id", "kind"), ("u1", "person"), ("x", "team"))}
    assert any("holds team" in p for p in answer_problems(answer, bad, "set"))


def test_plain_question_mode_accepts_the_shape_the_model_states() -> None:
    assert answer_problems(_answer("set", id_kind="issue"), {"r1": ISSUES}, None) == ()


# --- assemble (AC-18) ---


def test_number_and_boolean_are_the_single_cell() -> None:
    assert assemble(_answer("number", sources=[{"issue": ("r2", "id")}]), COUNT) == (
        2,
        ({"issue": ("CLOUD-1", "CLOUD-2")},),
    )
    yes = {"r1": _rows(("value",), (True,)), "r2": ISSUES}
    value, sources = assemble(_answer("boolean", sources=[{"issue": ("r2", "id")}]), yes)
    assert value is True
    assert sources == ({"issue": ("CLOUD-1", "CLOUD-2")},)


def test_map_and_ranking_keep_row_order_as_pairs_and_never_sum() -> None:
    results = {"r1": _rows(("id", "value"), ("u2", 7), ("u1", 7), ("u2", 1))}
    for shape in ("map", "ranking"):
        value, _ = assemble(_answer(shape, id_kind="person"), results)
        assert value == [("person:u2", 7), ("person:u1", 7), ("person:u2", 1)]
        assert not isinstance(value, dict)


def test_values_and_ids_come_from_every_stored_row() -> None:
    rows = tuple((f"CLOUD-{n}",) for n in range(60))
    results = {"r1": StoredResult(("id",), rows)}
    answer = _answer("set", id_kind="issue", sources=[{"issue": ("r1", "id")}])
    value, sources = assemble(answer, results)
    assert len(value) == 60
    assert len(sources[0]["issue"]) == 60


def test_int_ids_are_written_as_text() -> None:
    results = {"r1": _rows(("id",), (10001,)), "r2": _rows(("h",), (55,))}
    answer = _answer("set", id_kind="time_entry", sources=[{"time_entry": ("r2", "h")}])
    assert assemble(answer, results) == (["time_entry:10001"], ({"time_entry": ("55",)},))


@pytest.mark.parametrize(
    ("shape", "results"),
    [
        ("number", {"r1": _rows(("value",), (0,)), "r2": _rows(("id",))}),
        ("number", {"r1": _rows(("value",), (Decimal("0"),)), "r2": _rows(("id",))}),
        ("boolean", {"r1": _rows(("value",), (False,)), "r2": _rows(("id",))}),
        ("set", {"r1": _rows(("id",)), "r2": _rows(("id",))}),
    ],
)
def test_an_empty_zero_or_false_answer_with_no_source_ids_has_no_steps(
    shape: str, results: dict[str, StoredResult]
) -> None:
    answer = _answer(shape, id_kind="issue", sources=[{"issue": ("r2", "id")}])
    value, sources = assemble(answer, results)
    assert sources == ()
    assert store_answer(shape, value, sources)["sources"] == []


def test_a_non_empty_value_keeps_an_empty_step() -> None:
    # Q13's shape: a non empty value whose time_entry step points at no rows.
    results = {
        "r1": _rows(("value",), (3,)),
        "r2": ISSUES,
        "r3": _rows(("history_id",)),
    }
    answer = _answer(
        "number", sources=[{"issue": ("r2", "id")}, {"time_entry": ("r3", "history_id")}]
    )
    value, sources = assemble(answer, results)
    assert value == 3
    assert sources == ({"issue": ("CLOUD-1", "CLOUD-2")}, {"time_entry": ()})
    assert store_answer("number", value, sources)["sources"] == [
        {"issue": ["CLOUD-1", "CLOUD-2"]},
        {"time_entry": []},
    ]


def test_a_count_that_is_not_zero_keeps_its_steps_even_when_they_are_empty() -> None:
    results = {"r1": _rows(("value",), (Decimal("0.5"),)), "r2": _rows(("id",))}
    _, sources = assemble(_answer("number", sources=[{"issue": ("r2", "id")}]), results)
    assert sources == ({"issue": ()},)


@pytest.mark.parametrize(
    ("shape", "results", "id_kind"),
    [
        ("number", {"r1": _rows(("value",), (Decimal("2.5"),))}, None),
        ("map", {"r1": _rows(("id", "value"), ("CLOUD", 3))}, "project"),
        ("ranking", {"r1": _rows(("id", "value"), ("u1", 2.0))}, "person"),
        ("set", {"r1": ISSUES}, "issue"),
        ("boolean", {"r1": _rows(("value",), (True,))}, None),
    ],
)
def test_every_shape_goes_through_the_runner_converter(
    shape: str, results: dict[str, StoredResult], id_kind: str | None
) -> None:
    value, sources = assemble(_answer(shape, id_kind=id_kind), results)
    assert store_answer(shape, value, sources)["shape"] == shape


# --- Paths (AC-19) ---

PATHS = {
    "r1": _rows(
        ("path", "links", "issues"),
        (["A-1", "A-2", "A-3"], ["L1", ["L2", "L3"]], ["A-2", "A-3"]),
        (["B-1", "B-2"], ["L9"], ["B-2"]),
    )
}
PATH_ANSWER = _answer(
    "path", id_kind="issue", sources=[{"issue_link": ("r1", "links"), "issue": ("r1", "issues")}]
)


def test_a_path_splits_into_one_step_per_hop_per_path() -> None:
    assert answer_problems(PATH_ANSWER, PATHS, "path") == ()
    value, sources = assemble(PATH_ANSWER, PATHS)
    assert sources == ()
    assert value == [
        (
            ("issue:A-1", "issue:A-2", "issue:A-3"),
            (
                {"issue_link": ("L1",), "issue": ("A-2",)},
                {"issue_link": ("L2", "L3"), "issue": ("A-3",)},
            ),
        ),
        (("issue:B-1", "issue:B-2"), ({"issue_link": ("L9",), "issue": ("B-2",)},)),
    ]
    converted = store_answer("path", value, sources)
    assert converted["sources"] == []
    assert converted["value"][0]["sources"][1] == {"issue": ["A-3"], "issue_link": ["L2", "L3"]}


def test_a_wrong_hop_count_sends_the_model_back() -> None:
    bad = {"r1": _rows(("path", "links", "issues"), (["A-1", "A-2", "A-3"], ["L1"], ["A-2"]))}
    problems = answer_problems(PATH_ANSWER, bad, "path")
    assert any("has 1 hop items for a path of 3, it needs 2" in p for p in problems)


def test_a_path_source_outside_the_answer_result_or_a_second_step_is_refused() -> None:
    results = PATHS | {"r2": _rows(("links",), ("L1",))}
    elsewhere = _answer("path", id_kind="issue", sources=[{"issue_link": ("r2", "links")}])
    assert any("are in r1" in p for p in answer_problems(elsewhere, results, "path"))
    two = _answer(
        "path",
        id_kind="issue",
        sources=[{"issue_link": ("r1", "links")}, {"issue": ("r1", "issues")}],
    )
    assert any("exactly 1 source step" in p for p in answer_problems(two, PATHS, "path"))


def test_a_path_result_without_a_path_column_is_named_not_crashed() -> None:
    results = {"r1": _rows(("links",), (["L1"],))}
    problems = answer_problems(PATH_ANSWER, results, "path")
    assert any("needs column path" in p for p in problems)


# covers: AC-17, AC-19, the path checks that had no test
@pytest.mark.parametrize(
    ("row", "words"),
    [
        ((None, ["L1"], ["A-2"]), "column path of r1 holds NULL"),
        (("A-1>A-2", ["L1"], ["A-2"]), "must be a list of IDs"),
        ((["A-1", 2.5], ["L1"], ["A-2"]), "must be a list of IDs"),
        ((["A-1", "A-2"], "L1", ["A-2"]), "row 1 must hold a list with one ID or ID list per hop"),
        ((["A-1", "A-2"], [["L1", None]], ["A-2"]), "row 1 must hold a list"),
        ((["A-1", "A-2"], None, ["A-2"]), "column links of r1 holds NULL"),
    ],
)
def test_each_bad_path_row_sends_the_model_back(row: tuple[Any, ...], words: str) -> None:
    results = {"r1": _rows(("path", "links", "issues"), row)}
    problems = answer_problems(PATH_ANSWER, results, "path")
    assert any(words in problem for problem in problems), problems


# covers: AC-17, a mixed kind column is for map, ranking and set only
def test_a_path_with_a_kind_column_still_needs_id_kind() -> None:
    results = {"r1": _rows(("path", "kind", "links"), (["A-1", "A-2"], "issue", ["L1"]))}
    answer = _answer("path", sources=[{"issue_link": ("r1", "links")}])
    problems = answer_problems(answer, results, "path")
    assert any("a path answer needs id_kind" in p for p in problems), problems


# covers: AC-17, a NULL in column kind sends the model back and is not filtered
def test_a_null_in_the_kind_column_sends_the_model_back() -> None:
    results = {"r1": _rows(("id", "kind"), ("u1", "person"), ("CLOUD", None))}
    problems = answer_problems(_answer("set"), results, "set")
    assert any("column kind of r1 holds NULL" in p for p in problems), problems


# covers: AC-17, AC-18, a source column must hold IDs (str or int), not other values
@pytest.mark.parametrize("cell", [2.5, Decimal("3"), True, ["CLOUD-1"]])
def test_a_source_column_that_is_not_ids_sends_the_model_back(cell: Any) -> None:
    results = {"r1": ISSUES, "r2": _rows(("id",), ("u1",), (cell,))}
    answer = _answer("set", id_kind="issue", sources=[{"person": ("r2", "id")}])
    problems = answer_problems(answer, results, "set")
    assert any("column id of r2 must hold IDs" in p for p in problems), problems


# covers: AC-18, sources are dropped only when the value is empty AND every source list is empty
def test_an_empty_value_with_source_ids_keeps_its_steps() -> None:
    results = {"r1": _rows(("id",)), "r2": ISSUES}
    answer = _answer("set", id_kind="issue", sources=[{"issue": ("r2", "id")}])
    value, sources = assemble(answer, results)
    assert value == []
    assert sources == ({"issue": ("CLOUD-1", "CLOUD-2")},)


# --- The outcome map (AC-21) ---

OK = QueryAttempt("SELECT 1", "ok", 0.1, 1)
TIMED_OUT = QueryAttempt("SELECT 2", "store_timeout", 30.0, 0)


@pytest.mark.parametrize(
    ("end", "attempts", "outcome"),
    [
        ("final_answer", (OK,), "answered"),
        ("final_answer", (TIMED_OUT, OK), "answered"),
        ("final_answer", (OK, TIMED_OUT), "answered"),
        ("usage_limit", (OK,), "limit_hit"),
        ("usage_limit", (TIMED_OUT,), "limit_hit"),
        ("own_error", (TIMED_OUT,), "error"),
        ("plain_text", (OK, TIMED_OUT), "store_timeout"),
        ("model_failure", (TIMED_OUT,), "store_timeout"),
        ("plain_text", (TIMED_OUT, OK), "no_answer"),
        ("plain_text", (), "no_answer"),
        ("model_failure", (OK,), "no_answer"),
    ],
)
def test_each_end_maps_to_one_outcome(
    end: RunEnd, attempts: tuple[QueryAttempt, ...], outcome: str
) -> None:
    assert run_outcome(end, attempts) == outcome
