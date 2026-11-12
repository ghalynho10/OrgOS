"""Gold pairs with no store: the handoff types, the literal check, scoring, the Q01 adapter,
the tries rules, and every `gold try` and `gold decide` refusal (spec 0011 AC-11 to AC-17,
AC-21, AC-22, AC-25, AC-26).

The known answers here are made up in a temporary folder; the real `eval/answers.json`
is never read by these tests.
"""

import dataclasses
import inspect
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from orgos.config import Settings, load_settings
from orgos.eval import EvalError
from orgos.eval.answers import read_questions
from orgos.eval.compare import store_answer
from orgos.eval.frozen import ANSWERS, FROZEN, QUESTIONS, frozen_line, sha256_file
from orgos.eval.gold import (
    ACCEPTED,
    FileRun,
    Layout,
    explain_problems,
    gold_accept,
    gold_decide,
    gold_try,
    judge,
    layout,
    layout_message,
    parse_pins,
    parse_store,
    pin_problems,
    pinned,
    read_files,
    zero_guard_problems,
    zero_guarded,
)
from orgos.eval.gold_adapters import (
    ADAPTERS,
    Adapter,
    path_rows,
    q01,
    q02,
    q03,
    q04,
    q05,
    q06,
    q07,
    single,
    step,
)
from orgos.eval.handoff import (
    AgentDescription,
    AgentRun,
    QueryAgent,
    QueryAttempt,
    Usage,
)
from orgos.eval.literal import literal_problems, mask
from orgos.eval.score import MALFORMED, StepCount, score, step_counts
from orgos.eval.tries import (
    TRIES,
    append,
    citation_problem,
    pair_state,
    read_tries,
    reading_steps,
    schema_names,
)
from orgos.graphstore.mapping import load_mapping
from orgos.sqlstore.query import QueryResult

ROOT = Path(__file__).resolve().parents[1]

KNOWN_MAP: dict[str, Any] = {
    "shape": "map",
    "value": [
        {"id": "project:AAA", "value": {"int": 2}},
        {"id": "project:BBB", "value": {"int": 0}},
        {"id": "project:CCC", "value": {"int": 123456}},
    ],
    "sources": [{"issue": ["AAA-1", "AAA-2", "CCC-9"]}],
}
READING = (
    "Population: projects AAA, BBB and CCC.\n"
    "Sources, step by step: (1) select issues by project; (2) retain AAA-1 by name;"
    " (3) count per project.\n"
    "If empty: widen."
)
ALLOWED = f"How many issues per project?\n{READING}"


def result(columns: tuple[str, ...], *rows: tuple[Any, ...]) -> QueryResult:
    return QueryResult(
        columns=columns, rows=tuple(dict(zip(columns, row, strict=True)) for row in rows)
    )


# --- The handoff (AC-25, AC-26) ---


def test_no_handoff_type_holds_a_known_answer() -> None:
    for kind in (AgentDescription, QueryAttempt, Usage, AgentRun):
        names = {field.name for field in dataclasses.fields(kind)}
        assert not any("known" in name or "expected" in name for name in names), kind


def test_run_is_a_plain_method_not_a_coroutine() -> None:
    assert not inspect.iscoroutinefunction(QueryAgent.run)


def test_an_agent_run_keeps_an_empty_id_list() -> None:
    run = AgentRun(
        response_model=None,
        outcome="answered",
        shape="set",
        value=["issue:AAA-1"],
        sources=({"issue": ("AAA-1",), "time_entry": ()},),
        attempts=(QueryAttempt("SELECT 1", "ok", 0.1, 1),),
        usage=Usage(0, 0, 0, 1, 0.0),
        seconds=0.2,
    )
    assert run.sources[0]["time_entry"] == ()


# --- The literal check (AC-14) ---


def test_a_known_source_id_the_text_does_not_name_is_refused_by_line() -> None:
    query = "SELECT issue_key\nFROM model.issue\nWHERE issue_key = 'CCC-9'"
    problems = literal_problems((("q.sql", query),), KNOWN_MAP, ALLOWED)
    assert problems == ("q.sql:3: holds a literal from the known answer",)
    assert "CCC-9" not in problems[0]


def test_an_id_the_reading_note_names_is_allowed() -> None:
    query = "WHERE issue_key = 'AAA-1' AND project_key IN ('AAA', 'BBB', 'CCC')"
    assert literal_problems((("q.sql", query),), KNOWN_MAP, ALLOWED) == ()


def test_a_long_number_equal_to_a_known_number_is_refused() -> None:
    problems = literal_problems((("q.cypher", "RETURN 123456 AS n"),), KNOWN_MAP, ALLOWED)
    assert problems == ("q.cypher:1: holds a literal from the known answer",)


def test_a_small_number_is_not_caught() -> None:
    assert literal_problems((("q.sql", "SELECT 1 LIMIT 2"),), KNOWN_MAP, ALLOWED) == ()


def test_a_quoted_string_is_checked_whole() -> None:
    known = {"shape": "set", "value": ["issue:X Y"], "sources": [{"issue": ["X Y"]}]}
    problems = literal_problems((("q.sql", "WHERE k = 'X Y'"),), known, "")
    assert problems == ("q.sql:1: holds a literal from the known answer",)


PATH_KNOWN: dict[str, Any] = {
    "shape": "path",
    "value": [{"path": ["issue:P-1", "issue:P-7"], "sources": [{"issue_link": ["L-9"]}]}],
    "sources": [],
}


# covers: AC-14 (every shape's keys and IDs, and a fraction's long numbers, are checked)
@pytest.mark.parametrize(
    ("known", "query"),
    [
        (
            {"shape": "ranking", "value": [{"value": {"int": 4}, "ids": ["person:zed"]}]}
            | {"sources": []},
            "WHERE person_key = 'zed'",
        ),
        (PATH_KNOWN, "WHERE issue_key = 'P-7'"),
        (PATH_KNOWN, "WHERE link_id = 'L-9'"),
        ({"shape": "number", "value": {"num": 98765, "den": 3}, "sources": []}, "RETURN 98765"),
        ({"shape": "number", "value": {"num": 7, "den": 43210}, "sources": []}, "RETURN 43210"),
    ],
    ids=["ranking id", "path id", "path step id", "fraction num", "fraction den"],
)
def test_every_answer_shape_has_its_ids_and_long_numbers_checked(
    known: dict[str, Any], query: str
) -> None:
    problems = literal_problems((("q.cypher", query),), known, ALLOWED)
    assert problems == ("q.cypher:1: holds a literal from the known answer",)


def test_a_store_message_is_masked() -> None:
    assert mask('invalid input syntax: "CCC-9" near 123456', KNOWN_MAP) == (
        'invalid input syntax: "<hidden>" near <hidden>'
    )


# --- Scoring (AC-21, AC-22) ---


@pytest.mark.parametrize("raw", [math.nan, math.inf, Decimal("NaN"), "two"])
def test_a_bad_store_value_is_malformed_without_the_value(raw: object) -> None:
    scored = score(KNOWN_MAP, "map", [("project:AAA", raw)], [])
    assert (scored.answer_match, scored.source_match, scored.correct) == (False, False, False)
    assert scored.outcome == "malformed"
    assert scored.problem == MALFORMED
    assert str(raw) not in (scored.problem or "")


def test_a_right_answer_is_correct_with_zero_counts() -> None:
    value = [("project:AAA", 2), ("project:BBB", 0), ("project:CCC", 123456)]
    scored = score(KNOWN_MAP, "map", value, [{"issue": ["CCC-9", "AAA-2", "AAA-1"]}])
    assert scored.correct and scored.outcome == "correct"
    assert scored.steps == (StepCount(0, 0),)


def test_step_counts_are_counts_per_step() -> None:
    known = [{"issue": ["A", "B"]}, {"person": ["p"]}]
    store = [{"issue": ["B", "C", "D"]}]
    assert step_counts(known, store) == (StepCount(1, 2), StepCount(1, 0))


# --- The Q01 adapter (AC-12) ---


def test_the_adapter_adds_no_project_fills_no_zero_and_sums_nothing() -> None:
    answer = result(("project_key", "issues"), ("AAA", 2), ("AAA", 1), ("CCC", None))
    value, sources = q01(answer, (result(("issue",)),))
    assert value == [("project:AAA", 2), ("project:AAA", 1), ("project:CCC", None)]
    assert sources == [{"issue": []}]


def test_the_adapter_names_a_missing_column() -> None:
    with pytest.raises(EvalError, match="no column issues"):
        q01(result(("project_key", "n")), ())


# --- The Q02 to Q07 adapters (AC-12) ---


def test_single_returns_the_one_value_as_it_comes() -> None:
    assert single(result(("issues",), (Decimal("7"),)), "issues") == Decimal("7")
    assert single(result(("issues",), (0,)), "issues") == 0


@pytest.mark.parametrize("rows", [(), ((1,), (2,))], ids=["no row", "two rows"])
def test_single_refuses_any_row_count_but_one(rows: tuple[tuple[Any, ...], ...]) -> None:
    with pytest.raises(EvalError, match=f"returns {len(rows)} rows, not 1"):
        single(result(("issues",), *rows), "issues")


@pytest.mark.parametrize("adapter", [q02, q03], ids=["Q02", "Q03"])
def test_a_number_adapter_takes_the_one_count_and_every_step(adapter: Adapter) -> None:
    answer = result(("issues",), (3,))
    sources = (result(("issue",), ("A-1",), ("A-2",)), result(("issue",)))
    value, steps = adapter(answer, sources)
    assert value == 3
    assert steps == [{"issue": ["A-1", "A-2"]}, {"issue": []}]


@pytest.mark.parametrize("adapter", [q02, q03], ids=["Q02", "Q03"])
def test_a_number_adapter_refuses_more_than_one_row(adapter: Adapter) -> None:
    with pytest.raises(EvalError, match="returns 2 rows, not 1"):
        adapter(result(("issues",), (1,), (2,)), ())


def test_the_q04_adapter_keeps_each_difference_as_it_comes() -> None:
    answer = result(
        ("project_key", "difference"), ("AAA", Decimal("-5")), ("BBB", 0), ("CCC", None)
    )
    value, sources = q04(answer, (result(("issue", "time_entry"), ("A-1", None)),))
    assert value == [("project:AAA", Decimal("-5")), ("project:BBB", 0), ("project:CCC", None)]
    assert sources == [{"issue": ["A-1"], "time_entry": []}]


def test_the_q05_adapter_types_each_author_as_a_person() -> None:
    answer = result(("person_key", "seconds"), ("zed", Decimal("60")), ("amy", 0))
    value, sources = q05(answer, (result(("issue", "time_entry"), ("A-1", "h1")),))
    assert value == [("person:zed", Decimal("60")), ("person:amy", 0)]
    assert sources == [{"issue": ["A-1"], "time_entry": ["h1"]}]


def test_the_q06_adapter_keeps_row_order_ties_and_drops_the_reader_column() -> None:
    answer = result(
        ("project_key", "issue_key", "seconds"),
        ("BBB", "B-2", 90),
        ("AAA", "A-1", 30),
        ("AAA", "A-3", 30),
    )
    value, _ = q06(answer, ())
    assert value == [("issue:B-2", 90), ("issue:A-1", 30), ("issue:A-3", 30)]


def test_the_q07_adapter_keeps_row_order_and_each_difference() -> None:
    answer = result(("issue_key", "difference"), ("A-9", Decimal("120")), ("A-2", 0))
    value, sources = q07(answer, (result(("issue", "person", "time_entry")),))
    assert value == [("issue:A-9", Decimal("120")), ("issue:A-2", 0)]
    assert sources == [{"issue": [], "person": [], "time_entry": []}]


@pytest.mark.parametrize(
    ("adapter", "columns", "missing"),
    [
        (q04, ("project_key", "diff"), "difference"),
        (q05, ("author_key", "seconds"), "person_key"),
        (q06, ("issue_key", "total"), "seconds"),
        (q07, ("key", "difference"), "issue_key"),
    ],
    ids=["Q04", "Q05", "Q06", "Q07"],
)
def test_a_pairs_adapter_names_a_missing_column(
    adapter: Adapter, columns: tuple[str, ...], missing: str
) -> None:
    with pytest.raises(EvalError, match=f"no column {missing}"):
        adapter(result(columns, ("x", 1)), ())


# --- Empty cells in a sources step (AC-12, amended 2026-11-12) ---


def test_an_empty_cell_none_is_skipped() -> None:
    rows = result(("issue", "time_entry"), ("A-1", "h1"), ("A-2", None), ("A-3", "h3"))
    assert step(rows) == {"issue": ["A-1", "A-2", "A-3"], "time_entry": ["h1", "h3"]}


def test_an_empty_cell_column_of_only_none_gives_an_empty_list() -> None:
    rows = result(("issue", "time_entry"), ("A-1", None), ("A-2", None))
    assert step(rows) == {"issue": ["A-1", "A-2"], "time_entry": []}


def test_an_empty_cell_rule_keeps_an_empty_string_and_the_text_none() -> None:
    rows = result(("issue", "time_entry"), ("A-1", ""), ("A-2", "None"))
    assert step(rows) == {"issue": ["A-1", "A-2"], "time_entry": ["", "None"]}


def test_an_empty_cell_rule_leaves_rows_with_no_none_unchanged() -> None:
    rows = result(("issue", "time_entry"), ("A-1", "h1"), ("A-1", "h2"), ("A-2", 7))
    assert step(rows) == {"issue": ["A-1", "A-1", "A-2"], "time_entry": ["h1", "h2", 7]}
    assert step(rows) == {kind: [row[kind] for row in rows.rows] for kind in rows.columns}


# --- Path rows (AC-12, amended 2026-11-12 for feature 15) ---


def test_path_rows_give_one_step_per_hop_and_wrap_a_single_id() -> None:
    answer = result(
        ("path", "issue_link", "issue"),
        (["A-1", "A-2", "A-3", "A-4"], ["l1", ["l2", "l3"], "l4"], [["A-1", "A-2"], "x", None]),
    )
    ((path, hops),) = path_rows(answer, "issue")
    assert path == ["issue:A-1", "issue:A-2", "issue:A-3", "issue:A-4"]
    assert hops == [
        {"issue_link": ["l1"], "issue": ["A-1", "A-2"]},
        {"issue_link": ["l2", "l3"], "issue": ["x"]},
        {"issue_link": ["l4"], "issue": []},
    ]


def test_path_rows_keep_an_empty_string_and_the_text_none_as_ids() -> None:
    answer = result(("path", "issue_link"), (["A-1", "A-2", "A-3"], ["", ["None", None]]))
    ((_, hops),) = path_rows(answer, "issue")
    assert hops == [{"issue_link": [""]}, {"issue_link": ["None"]}]


def test_path_rows_do_not_sort_filter_or_dedupe_rows() -> None:
    answer = result(
        ("path", "issue_link"),
        (["B-2", "A-1"], ["l1"]),
        (["A-1", "B-2"], ["l1"]),
        (["A-1", "B-2"], ["l1"]),
    )
    rows = path_rows(answer, "issue")
    assert [path for path, _ in rows] == [
        ["issue:B-2", "issue:A-1"],
        ["issue:A-1", "issue:B-2"],
        ["issue:A-1", "issue:B-2"],
    ]


def test_path_rows_through_the_adapter_keep_top_level_sources_empty() -> None:
    answer = result(("path", "issue_link", "issue"), (["A-1", "A-2"], ["l1"], [["A-1", "A-2"]]))
    stored = store_answer("path", path_rows(answer, "issue"), [])
    assert stored["sources"] == []
    assert stored["value"] == [
        {
            "path": ["issue:A-1", "issue:A-2"],
            "sources": [{"issue": ["A-1", "A-2"], "issue_link": ["l1"]}],
        }
    ]


@pytest.mark.parametrize(
    ("columns", "row", "message"),
    [
        (
            ("path", "issue_link"),
            (["SEC-1", "SEC-2", "SEC-3"], ["SEC-9"]),
            "holds 1 items for a path of 3 IDs, it needs 2",
        ),
        (
            ("path", "issue_link"),
            (["SEC-1", "SEC-2"], None),
            "column issue_link is NULL or not a list",
        ),
        (("path", "issue_link"), (None, ["SEC-9"]), "column path is NULL or not a list"),
        (("issue", "issue_link"), (["SEC-1"], ["SEC-9"]), "no column path"),
        (
            ("path", "issue_link", "label"),
            (["SEC-1", "SEC-2"], ["SEC-9"], "SEC-blocks"),
            "columns label are neither path nor a kind",
        ),
    ],
    ids=["wrong_length", "null_cell", "null_path", "missing_path", "not_a_kind"],
)
def test_path_rows_refuse_a_malformed_row_with_no_value(
    columns: tuple[str, ...], row: tuple[Any, ...], message: str
) -> None:
    with pytest.raises(EvalError, match=message) as excinfo:
        path_rows(result(columns, row), "issue")
    assert "SEC-" not in str(excinfo.value)


# --- The layout message of a path pair (AC-11, AC-13, amended 2026-11-12) ---


def test_layout_message_path_names_columns_and_kind_sets_and_no_count() -> None:
    known = {
        "shape": "path",
        "sources": [],
        "value": [
            {
                "path": ["issue:SEC-1", "issue:SEC-2", "issue:SEC-3"],
                "sources": [
                    {"issue_link": ["SEC-77"], "issue": ["SEC-1", "SEC-2"]},
                    {"issue": ["SEC-2", "SEC-3"], "issue_link": ["SEC-78"]},
                ],
            },
            {
                "path": ["issue:SEC-4", "issue:SEC-5"],
                "sources": [{"issue": ["SEC-4", "SEC-5"], "issue_link": []}],
            },
        ],
    }
    question = next(q for q in read_questions(ROOT) if q.id == "Q08")
    pair = layout(question, known, "sql")
    assert pair.path_kinds == (("issue", "issue_link"),)
    assert pair.query_files == (Path("gold/sql/Q08.answer.sql"),)
    message = layout_message(pair, pair.query_files)
    assert "a `path` column" in message
    assert "one list column per kind, named after the kind, with one item per hop" in message
    assert "Kinds per hop in the known answer: issue, issue_link." in message
    assert "SEC-" not in message
    assert "source step" not in message
    assert not any(char.isdigit() for char in message.replace("Q08", ""))


def test_layout_message_path_leaves_a_non_path_pair_unchanged() -> None:
    message = layout_message(PAIR, PAIR.query_files)
    assert message == (
        "Q01 sql: missing gold/sql/Q01.answer.sql, gold/sql/Q01.sources.1.sql.\n"
        "The known answer has 1 source step (step 1: issue).\n"
        "Each sources file returns one column per kind of its step, named after the kind,"
        " with one ID per row.\n"
        "Expected files: gold/sql/Q01.answer.sql, gold/sql/Q01.sources.1.sql"
    )


def test_every_question_with_gold_files_has_an_adapter() -> None:
    drafted = {path.name.split(".")[0] for path in (ROOT / "gold").glob("*/Q*.answer.*")}
    assert drafted <= set(ADAPTERS)


# --- Judging a pair (AC-13, AC-18, AC-21) ---

PAIR = Layout(question_id="Q01", store="sql", kinds=(("issue",),), stages=())


def ok(path: str, query_result: QueryResult) -> FileRun:
    return FileRun(path, "ok", 0.01, query_result, None)


def test_a_timed_out_file_fails_the_pair_with_its_label() -> None:
    runs = (FileRun("a.sql", "store_timeout", 1.0, None, "the SQL query ran past the 1 s"),)
    judged = judge(PAIR, KNOWN_MAP, q01, runs)
    assert judged.scored.outcome == "store_timeout" and not judged.scored.correct


def test_a_store_error_is_masked_in_the_problem() -> None:
    runs = (FileRun("a.sql", "error", 0.1, None, 'bad value "CCC-9"\nLINE 1: ...'),)
    judged = judge(PAIR, KNOWN_MAP, q01, runs)
    assert judged.scored.outcome == "error"
    assert judged.problem == 'a.sql: bad value "<hidden>"'


def test_a_wrong_pair_is_wrong_and_a_missing_column_is_malformed() -> None:
    answer = result(("project_key", "issues"), ("AAA", 2), ("BBB", 0))
    sources = result(("issue",), ("AAA-1",))
    judged = judge(PAIR, KNOWN_MAP, q01, (ok("a", answer), ok("s", sources)))
    assert judged.scored.outcome == "wrong"
    assert judged.scored.steps == (StepCount(2, 0),)
    bad = judge(PAIR, KNOWN_MAP, q01, (ok("a", result(("k",))), ok("s", sources)))
    assert bad.scored.outcome == "malformed"
    assert bad.problem == "Q01: the query returns no column project_key (it returns k)"


def test_stage_counts_are_kept_and_a_non_integer_stage_is_malformed() -> None:
    zero = {"shape": "number", "value": {"int": 0}, "sources": []}
    assert zero_guarded(zero) and not zero_guarded(KNOWN_MAP)
    pair = Layout("Q03", "sql", (), (1, 2))
    adapter = ADAPTERS["Q01"]

    def number(answer: QueryResult, _: tuple[QueryResult, ...]) -> tuple[Any, list[Any]]:
        return answer.rows[0]["n"], []

    runs = (
        ok("a", result(("n",), (0,))),
        ok("st1", result(("n",), (40,))),
        ok("st2", result(("n",), (0,))),
    )
    judged = judge(pair, zero, number, runs)
    assert judged.scored.correct and judged.stage_counts == (40, 0)
    bad = (*runs[:2], ok("st2", result(("n", "m"), (0, 1))))
    assert judge(pair, zero, adapter, bad).scored.outcome == "malformed"


# --- The tries rules (AC-15 to AC-17) ---


def test_the_q01_reading_note_numbers_five_steps() -> None:
    question = next(q for q in read_questions(ROOT) if q.id == "Q01")
    assert reading_steps(question.reading) == (1, 2, 3, 4, 5)


def try_line(round_number: int, number: int, correct: bool) -> dict[str, Any]:
    return {
        "event": "try",
        "question": "Q01",
        "store": "sql",
        "round": round_number,
        "try": number,
        "correct": correct,
    }


def test_three_failed_tries_block_and_a_rewrite_opens_round_two() -> None:
    lines = [try_line(1, n, False) for n in (1, 2, 3)]
    state = pair_state(lines, "Q01", "sql")
    assert state.blocked and state.next_try == 4
    lines.append({"event": "decide", "question": "Q01", "store": "sql", "decision": "rewrite"})
    state = pair_state(lines, "Q01", "sql")
    assert (state.round, state.next_try, state.blocked) == (2, 1, False)
    assert pair_state(lines, "Q01", "graph").next_try == 1


def test_a_correct_try_does_not_count_as_failed() -> None:
    lines = [try_line(1, 1, False), try_line(1, 2, True), try_line(1, 3, False)]
    state = pair_state(lines, "Q01", "sql")
    assert state.failed == 2 and not state.blocked


NAMES = frozenset({"issue", "resolution_name", "Issue", "ASSIGNED_TO", "resolutionName"})


@pytest.mark.parametrize("cites", ["step 3", "resolution_name", "ASSIGNED_TO", "resolutionName"])
def test_a_valid_citation_passes(cites: str) -> None:
    assert citation_problem("the bound was wrong", cites, (1, 2, 3), NAMES) is None


@pytest.mark.parametrize(
    ("hypothesis", "cites", "reason"),
    [
        (None, "step 1", "--hypothesis is required"),
        ("x", None, "--cites is required"),
        ("x", "the counts", "not `step <n>`"),
        ("x", "step 9", "numbers its source steps 1, 2, 3"),
    ],
)
def test_a_retry_without_a_valid_citation_is_refused(
    hypothesis: str | None, cites: str | None, reason: str
) -> None:
    problem = citation_problem(hypothesis, cites, (1, 2, 3), NAMES)
    assert problem is not None and reason in problem


def test_schema_names_hold_the_model_tables_columns_and_the_mapping() -> None:
    names = schema_names(ROOT, load_mapping())
    assert {"issue", "logged_time", "resolution_name", "Issue", "IN_PROJECT", "issueKey"} <= names


def test_the_log_is_appended_never_rewritten(tmp_path: Path) -> None:
    append(tmp_path, try_line(1, 1, False))
    first = (tmp_path / TRIES).read_text()
    append(tmp_path, try_line(1, 2, True))
    assert (tmp_path / TRIES).read_text().startswith(first)
    assert [line["try"] for line in read_tries(tmp_path)] == [1, 2]


# --- `gold try` and `gold decide` refusals, before any store (AC-13, AC-16, AC-17) ---

QUESTION_TOML = '''
[[question]]
id = "Q01"
text = "How many issues per project?"
type = "aggregate"
hops = 0
hop_mode = "fixed"
shape = "map"
unit = "count"
fields = ["fields.project.key"]
reading = """
{reading}
"""
data = "real"
tracer = true
'''


def freeze(root: Path) -> None:
    """Pin the made up eval files in `eval/FROZEN`, as the gold commands check it first."""
    (root / FROZEN).write_text(
        "".join(frozen_line(root, path) + "\n" for path in (QUESTIONS, ANSWERS))
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/questions.toml").write_text(QUESTION_TOML.format(reading=READING))
    answers = {"amendments_sha256": None, "answers": {"Q01": KNOWN_MAP}, "questions_sha256": "x"}
    (tmp_path / "eval/answers.json").write_text(json.dumps(answers))
    freeze(tmp_path)
    (tmp_path / "dbt/models/model").mkdir(parents=True)
    (tmp_path / "dbt/models/model/model.yml").write_text("models:\n  - name: issue\n")
    return tmp_path


def settings() -> Settings:
    return load_settings(
        _env_file=None,
        POSTGRES_PASSWORD="unused",
        ORGOS_PG_OWNER_PASSWORD="unused",
        ORGOS_PG_READER_PASSWORD="unused",
        MONGO_INITDB_ROOT_PASSWORD="unused",
        ORGOS_MONGO_READER_PASSWORD="unused",
        NEO4J_AUTH="neo4j/unused-password",
    )


def write_pair(root: Path) -> None:
    (root / "gold/sql").mkdir(parents=True)
    (root / "gold/sql/Q01.answer.sql").write_text("SELECT 1")
    (root / "gold/sql/Q01.sources.1.sql").write_text("SELECT 1")


def test_a_missing_file_is_refused_with_the_layout_and_no_id(root: Path) -> None:
    with pytest.raises(EvalError) as excinfo:
        gold_try(settings(), root, "Q01", "sql", hypothesis=None, cites=None)
    message = str(excinfo.value)
    assert "missing gold/sql/Q01.answer.sql, gold/sql/Q01.sources.1.sql" in message
    assert "1 source step (step 1: issue)" in message
    assert not any(token in message for token in ("AAA-1", "CCC-9", "123456", "project:"))
    assert not (root / TRIES).exists()


# covers: the review finding on the order (the frozen set is checked before any answer is read)
@pytest.mark.parametrize("command", ["try", "accept"])
def test_a_broken_frozen_set_refuses_before_the_answers_are_read(root: Path, command: str) -> None:
    files = accept_root(root)
    append(root, correct_try(files))
    (root / ANSWERS).write_text("not the frozen answers")
    with pytest.raises(EvalError, match="Preflight refused at the frozen set check") as excinfo:
        if command == "try":
            gold_try(settings(), root, "Q01", "sql", hypothesis="h", cites="step 1")
        else:
            gold_accept(root, "Q01", "sql", reviewed=True)
    assert f"{ANSWERS} differs from its hash in {FROZEN}" in str(excinfo.value)
    assert len(read_tries(root)) == 1
    assert not (root / ACCEPTED).exists()


# covers: the review finding on the hash (taken from the bytes already read, not a second read)
def test_each_hash_comes_from_the_bytes_its_text_was_read_from(tmp_path: Path) -> None:
    (tmp_path / "Q01.answer.sql").write_bytes(b"SELECT 1\r\nFROM t\r\n")
    ((path, text, sha),) = read_files(tmp_path, (Path("Q01.answer.sql"),))
    assert (path, text) == ("Q01.answer.sql", "SELECT 1\r\nFROM t\r\n")
    assert sha == sha256_file(tmp_path / "Q01.answer.sql")


def test_accept_pins_the_hashes_of_what_it_read_without_reading_again(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    append(root, correct_try(accept_root(root)))
    expected = {path: sha256_file(root / path) for path in ACCEPT_FILES}

    def no_second_read(path: Path) -> str:
        raise AssertionError(f"{path} was hashed by a second read")

    monkeypatch.setattr("orgos.eval.gold.sha256_file", no_second_read)
    gold_accept(root, "Q01", "sql", reviewed=True)
    assert read_tries(root)[-1]["files"] == expected


def test_the_layout_names_stage_files_when_zero_guarded() -> None:
    message = layout_message(Layout("Q03", "graph", (), (1, 2)), ())
    assert "gold/cypher/Q03.stage.1.cypher, gold/cypher/Q03.stage.2.cypher" in message


def test_a_fourth_try_in_a_round_is_refused_naming_gold_decide(root: Path) -> None:
    write_pair(root)
    for number in (1, 2, 3):
        append(root, try_line(1, number, False))
    with pytest.raises(EvalError, match="run `orgos eval gold decide Q01 --store sql`"):
        gold_try(settings(), root, "Q01", "sql", hypothesis="h", cites="step 1")


def test_a_second_try_without_a_citation_is_refused_and_not_logged(root: Path) -> None:
    write_pair(root)
    append(root, try_line(1, 1, False))
    with pytest.raises(EvalError, match="--hypothesis is required"):
        gold_try(settings(), root, "Q01", "sql", hypothesis=None, cites=None)
    with pytest.raises(EvalError, match="not `step <n>`"):
        gold_try(settings(), root, "Q01", "sql", hypothesis="h", cites="the counts")
    assert len(read_tries(root)) == 1


def test_decide_refuses_an_unblocked_pair_then_rewrites_and_records(root: Path) -> None:
    with pytest.raises(EvalError, match="is not blocked: 0 failed tries in round 1"):
        gold_decide(root, "Q01", "sql", rewrite=True, discrepancy=None)
    for number in (1, 2, 3):
        append(root, try_line(1, number, False))
    assert "round 2 opens" in gold_decide(root, "Q01", "sql", rewrite=True, discrepancy=None)
    for number in (1, 2, 3):
        append(root, try_line(2, number, False))
    gold_decide(root, "Q01", "sql", rewrite=False, discrepancy="the dump disagrees")
    write_pair(root)
    with pytest.raises(EvalError, match="known answer discrepancy"):
        gold_try(settings(), root, "Q01", "sql", hypothesis="h", cites="step 1")


def test_decide_needs_exactly_one_choice(root: Path) -> None:
    with pytest.raises(EvalError, match="exactly one"):
        gold_decide(root, "Q01", "sql", rewrite=True, discrepancy="both")


def test_the_store_is_sql_or_graph() -> None:
    assert parse_store("graph") == "graph"
    with pytest.raises(EvalError, match="sql or graph"):
        parse_store("cypher")


# --- The zero guard, the explain file and `gold accept` (AC-18 to AC-20, AC-37) ---

LOAD_ID = "1762000000.123456"
EXPLAIN = "Clause one is step 1, then step 2, and the count is step 3.\n"
ACCEPT_FILES = ("gold/sql/Q01.answer.sql", "gold/sql/Q01.explain.md", "gold/sql/Q01.sources.1.sql")


def accept_root(root: Path, *, explain: str = EXPLAIN) -> dict[str, str]:
    write_pair(root)
    (root / "gold/sql/Q01.explain.md").write_text(explain)
    for path in ("results/sql_load/run_record.json", "results/graph_load/run_record.json"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(json.dumps({"status": "ok", "dlt_load_id": LOAD_ID}))
    return {
        path: sha256_file(root / path)
        for path in ("gold/sql/Q01.answer.sql", "gold/sql/Q01.sources.1.sql")
    }


def correct_try(files: dict[str, str], load_id: str = LOAD_ID) -> dict[str, Any]:
    return try_line(1, 1, True) | {"files": files, "load_id": load_id}


def test_a_correct_reviewed_pair_is_pinned_and_logged(root: Path) -> None:
    append(root, correct_try(accept_root(root)))
    lines = gold_accept(root, "Q01", "sql", reviewed=True)
    assert lines == ("Q01 sql: accepted on round 1, try 1; 3 files pinned in gold/ACCEPTED",)
    pins = [path for _, path in parse_pins((root / ACCEPTED).read_text())]
    assert pins == [
        "gold/sql/Q01.answer.sql",
        "gold/sql/Q01.explain.md",
        "gold/sql/Q01.sources.1.sql",
    ]
    assert pin_problems(root) == ()
    accepted = read_tries(root)[-1]
    assert (accepted["event"], accepted["round"], accepted["try"]) == ("accept", 1, 1)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("stale", "a query file changed since round 1, try 1"),
        ("explain", "the explain file never names step 3"),
        ("unreviewed", "pass --reviewed"),
        ("load", f"a try holds load ID 1761000000.1, the current load ID is {LOAD_ID}"),
        ("wrong", "the latest try (round 1, try 1) was not correct"),
    ],
)
def test_accept_refuses_each_unmet_condition(root: Path, change: str, reason: str) -> None:
    explain = "Only step 1 and step 2.\n" if change == "explain" else EXPLAIN
    files = accept_root(root, explain=explain)
    line = correct_try(files, "1761000000.1" if change == "load" else LOAD_ID)
    append(root, line | {"correct": change != "wrong"})
    if change == "stale":
        (root / "gold/sql/Q01.answer.sql").write_text("SELECT 2")
    with pytest.raises(EvalError, match="is not accepted") as excinfo:
        gold_accept(root, "Q01", "sql", reviewed=change != "unreviewed")
    assert reason in str(excinfo.value)
    assert not (root / ACCEPTED).exists()


def test_accept_refuses_a_missing_explain_file(root: Path) -> None:
    files = accept_root(root)
    (root / "gold/sql/Q01.explain.md").unlink()
    append(root, correct_try(files))
    with pytest.raises(EvalError, match=r"missing gold/sql/Q01\.explain\.md"):
        gold_accept(root, "Q01", "sql", reviewed=True)


# covers: AC-19 (only the latest try of the current round can be accepted)
@pytest.mark.parametrize(
    ("tries", "reason"),
    [
        ([True, False], "the latest try (round 1, try 2) was not correct"),
        ([True, False, False, False, "rewrite"], "no try yet in round 2"),
    ],
    ids=["a later failed try", "an earlier round"],
)
def test_a_correct_try_counts_only_while_it_is_the_latest_of_the_round(
    root: Path, tries: list[bool | str], reason: str
) -> None:
    files = accept_root(root)
    for number, item in enumerate(tries, 1):
        if item == "rewrite":
            gold_decide(root, "Q01", "sql", rewrite=True, discrepancy=None)
        else:
            append(root, try_line(1, number, bool(item)) | {"files": files, "load_id": LOAD_ID})
    with pytest.raises(EvalError, match="is not accepted") as excinfo:
        gold_accept(root, "Q01", "sql", reviewed=True)
    assert reason in str(excinfo.value)
    assert not (root / ACCEPTED).exists()


def test_accepting_one_pair_keeps_the_other_pins() -> None:
    cypher, sql = "gold/cypher/Q01.answer.cypher", "gold/sql/Q01.answer.sql"
    text = pinned((("a" * 64, cypher), ("b" * 64, sql)), PAIR, {sql: "c" * 64})
    assert text == f"{'a' * 64}  {cypher}\n{'c' * 64}  {sql}\n"


@pytest.mark.parametrize(
    ("sql", "graph", "problem"),
    [
        ((40, 3), (40, 3), None),
        (None, (40, 3), "no correct try yet in sql"),
        ((40, 3), (41, 3), "stage counts differ at step 1: sql 40, graph 41"),
        ((0, 0), (0, 0), "the step 1 count is 0, not above 0"),
    ],
)
def test_the_zero_guard(
    sql: tuple[int, ...] | None, graph: tuple[int, ...] | None, problem: str | None
) -> None:
    problems = zero_guard_problems((1, 2), sql, graph)
    if problem is None:
        assert problems == ()
    else:
        assert len(problems) == 1 and problem in problems[0]


ZERO_READING = (
    "Population: issues of project AAA.\n"
    "Sources, step by step: (1) select issues by project; (2) retain the resolved.\n"
    "If empty: the answer is 0."
)
ZERO_PARTS = ("answer", "stage.1", "stage.2")


@pytest.fixture
def zero_root(tmp_path: Path) -> Path:
    (tmp_path / "eval").mkdir()
    toml = QUESTION_TOML.format(reading=ZERO_READING)
    toml = toml.replace('id = "Q01"', 'id = "Q03"').replace('shape = "map"', 'shape = "number"')
    (tmp_path / "eval/questions.toml").write_text(toml)
    zero = {"shape": "number", "value": {"int": 0}, "sources": []}
    answers = {"amendments_sha256": None, "answers": {"Q03": zero}, "questions_sha256": "x"}
    (tmp_path / "eval/answers.json").write_text(json.dumps(answers))
    freeze(tmp_path)
    for folder in ("sql", "cypher"):
        (tmp_path / "gold" / folder).mkdir(parents=True)
        for part in ZERO_PARTS:
            (tmp_path / f"gold/{folder}/Q03.{part}.{folder}").write_text(f"-- {part}\n")
        (tmp_path / f"gold/{folder}/Q03.explain.md").write_text("step 1, then step 2.\n")
    for path in ("results/sql_load/run_record.json", "results/graph_load/run_record.json"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(json.dumps({"status": "ok", "dlt_load_id": LOAD_ID}))
    return tmp_path


def zero_try(root: Path, store: str, number: int, counts: list[int]) -> dict[str, Any]:
    folder = "sql" if store == "sql" else "cypher"
    paths = (f"gold/{folder}/Q03.{part}.{folder}" for part in ZERO_PARTS)
    return {
        "event": "try",
        "question": "Q03",
        "store": store,
        "round": 1,
        "try": number,
        "correct": True,
        "outcome": "correct",
        "files": {path: sha256_file(root / path) for path in paths},
        "load_id": LOAD_ID,
        "stage_counts": counts,
    }


# covers: AC-18 (accept reads both stores' latest correct stage counts)
def test_a_zero_guarded_pair_is_accepted_only_once_both_stores_agree(zero_root: Path) -> None:
    append(zero_root, zero_try(zero_root, "sql", 1, [40, 3]))
    with pytest.raises(EvalError, match="no correct try yet in graph"):
        gold_accept(zero_root, "Q03", "sql", reviewed=True)
    append(zero_root, zero_try(zero_root, "graph", 1, [41, 3]))
    with pytest.raises(EvalError, match="stage counts differ at step 1: sql 40, graph 41"):
        gold_accept(zero_root, "Q03", "sql", reviewed=True)
    assert not (zero_root / ACCEPTED).exists()
    append(zero_root, zero_try(zero_root, "graph", 2, [40, 3]))
    (line,) = gold_accept(zero_root, "Q03", "sql", reviewed=True)
    assert line.startswith("Q03 sql: accepted on round 1, try 1")


def test_an_explain_file_must_name_every_step() -> None:
    assert explain_problems("step 1, step 2 and **step 3**", (1, 2, 3)) == ()
    missing = explain_problems("step 1 and step 12", (1, 2))
    assert missing == ("the explain file never names step 2",)
