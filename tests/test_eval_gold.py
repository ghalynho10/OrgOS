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
from orgos.eval.frozen import sha256_file
from orgos.eval.gold import (
    ACCEPTED,
    FileRun,
    Layout,
    explain_problems,
    gold_accept,
    gold_decide,
    gold_try,
    judge,
    layout_message,
    parse_pins,
    parse_store,
    pin_problems,
    pinned,
    zero_guard_problems,
    zero_guarded,
)
from orgos.eval.gold_adapters import ADAPTERS, q01
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


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/questions.toml").write_text(QUESTION_TOML.format(reading=READING))
    answers = {"amendments_sha256": None, "answers": {"Q01": KNOWN_MAP}, "questions_sha256": "x"}
    (tmp_path / "eval/answers.json").write_text(json.dumps(answers))
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


def test_an_explain_file_must_name_every_step() -> None:
    assert explain_problems("step 1, step 2 and **step 3**", (1, 2, 3)) == ()
    missing = explain_problems("step 1 and step 12", (1, 2))
    assert missing == ("the explain file never names step 2",)
