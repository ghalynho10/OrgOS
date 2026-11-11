"""Agent runs with a stub agent and no store: the summary, run files, the plan and cost
gate, the versions log, agent loading and outages (spec 0011 AC-23 to AC-35, AC-37, AC-38).

No test here makes a model call: the agent is `StubAgent`, loaded by its dotted path.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from orgos.config import Settings, load_settings
from orgos.eval import EvalError
from orgos.eval.answers import read_questions
from orgos.eval.frozen import sha256_file
from orgos.eval.gold import gold_entries
from orgos.eval.handoff import (
    AgentDescription,
    AgentRun,
    ProviderOutage,
    QueryAttempt,
    Store,
    StoreOutage,
    Usage,
)
from orgos.eval.models import Question
from orgos.eval.replay import accepted_files
from orgos.eval.runs import (
    PLAN,
    VERSIONS,
    Plan,
    Work,
    estimate,
    gate_problem,
    limit_reached,
    load_agent,
    load_id_problems,
    make_plan,
    planned_work,
    read_run_files,
    retries,
    run_entries,
    run_path,
    run_work,
    score_run,
    stored_answer,
    version_add,
    version_entry,
    write_once,
)
from orgos.eval.score import Entry, groups, summary_lines, versions_statement
from tests.test_eval_gold import KNOWN_MAP, QUESTION_TOML, READING

LOAD_ID = "1762000000.123456"
DESCRIPTION = AgentDescription(
    model_id="stub:model-2026-01-01",
    version_hash="v1",
    parts={"instructions": "a" * 64, "tools": "b" * 64},
    price_table="prices.toml@2026-01-01",
    usd_per_million_input=3.0,
    usd_per_million_output=15.0,
)
USAGE = Usage(input_tokens=1000, output_tokens=200, requests=2, tool_calls=1, cost_usd=0.01)
RIGHT = [("project:AAA", 2), ("project:BBB", 0), ("project:CCC", 123456)]


def answered(
    value: Any = RIGHT, sources: Any = ({"issue": ("AAA-1", "AAA-2", "CCC-9")},)
) -> AgentRun:
    return AgentRun(
        response_model="stub-model",
        outcome="answered",
        shape="map",
        value=value,
        sources=sources,
        attempts=(QueryAttempt("SELECT 1", "ok", 0.1, 3),),
        usage=USAGE,
        seconds=0.5,
    )


@dataclass(frozen=True)
class StubAgent:
    """A `QueryAgent` that answers from a script and never calls a model."""

    script: tuple[AgentRun | Exception, ...] = ()
    description: AgentDescription = DESCRIPTION
    # Each stub keeps its own calls; the list is mutated, the field never reassigned.
    calls: list[str] = field(default_factory=list)

    def describe(self) -> AgentDescription:
        return self.description

    def run(self, question: Question, store: Store) -> AgentRun:
        item = self.script[len(self.calls)] if len(self.calls) < len(self.script) else answered()
        self.calls.append(question.id)
        if isinstance(item, Exception):
            raise item
        return item


def stub_factory(settings: Settings) -> StubAgent:
    return StubAgent()


def not_an_agent(settings: Settings) -> object:
    return object()


def settings(**overrides: object) -> Settings:
    return load_settings(
        _env_file=None,
        POSTGRES_PASSWORD="unused",
        ORGOS_PG_OWNER_PASSWORD="unused",
        ORGOS_PG_READER_PASSWORD="unused",
        MONGO_INITDB_ROOT_PASSWORD="unused",
        ORGOS_MONGO_READER_PASSWORD="unused",
        NEO4J_AUTH="neo4j/unused-password",
        **overrides,
    )


LIMITS = {
    "ORGOS_AGENT_REQUEST_LIMIT": 10,
    "ORGOS_AGENT_TOKEN_LIMIT": 50_000,
    "ORGOS_AGENT_TOOL_CALL_LIMIT": 8,
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    for args in (
        ("init", "--quiet"),
        (
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "empty",
        ),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True)
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/questions.toml").write_text(QUESTION_TOML.format(reading=READING))
    answers = {"amendments_sha256": None, "answers": {"Q01": KNOWN_MAP}, "questions_sha256": "x"}
    (tmp_path / "eval/answers.json").write_text(json.dumps(answers))
    (tmp_path / "src/orgos/eval").mkdir(parents=True)
    (tmp_path / "src/orgos/eval/compare.py").write_text("# the comparison rule\n")
    return tmp_path


def work(root: Path, *numbers: int, store: Store = "sql") -> tuple[Work, ...]:
    question = read_questions(root)[0]
    return tuple(Work(question, store, number) for number in numbers)


def execute(
    root: Path, agent: StubAgent, items: tuple[Work, ...], cap: float | None = None
) -> tuple[int, float, list[str]]:
    return run_work(
        root,
        settings(),
        agent,
        items,
        version="v1",
        plan=None,
        load_id=LOAD_ID,
        known={"Q01": KNOWN_MAP},
        cap=cap,
    )


# --- Scoring a run and counting its trace (AC-10, AC-26 to AC-28) ---


def test_attempts_and_retries_come_from_the_trace() -> None:
    attempts = (
        QueryAttempt("a", "error", 0.1, 0),
        QueryAttempt("b", "store_timeout", 1.0, 0),
        QueryAttempt("c", "ok", 0.1, 5),
        QueryAttempt("d", "ok", 0.1, 5),
    )
    assert retries(attempts) == 2
    assert retries(()) == 0


@pytest.mark.parametrize("outcome", ["no_answer", "limit_hit", "store_timeout", "error"])
def test_a_run_without_an_answer_is_scored_against_the_agent(outcome: str) -> None:
    run = replace(answered(), outcome=outcome, shape=None, value=None, sources=())
    scored = score_run(KNOWN_MAP, run)
    assert scored.outcome == outcome and not scored.correct


def test_an_answered_run_is_scored_by_compare() -> None:
    assert score_run(KNOWN_MAP, answered()).outcome == "correct"
    assert score_run(KNOWN_MAP, answered(value=RIGHT[:2])).outcome == "wrong"
    assert (
        score_run(KNOWN_MAP, answered(value=[("project:AAA", float("nan"))])).outcome == "malformed"
    )


PLAN_VALUES = Plan(3, 10, 50_000, 8, 30, "stub", "t", 3.0, 15.0, 1.0)


@pytest.mark.parametrize(
    ("usage", "limit"),
    [
        (Usage(10, 10, 10, 8, 0.0), "requests"),
        (Usage(10, 10, 3, 8, 0.0), "tool_calls"),
        (Usage(40_000, 10_000, 3, 2, 0.0), "tokens"),
        (Usage(10, 10, 3, 2, 0.0), None),
    ],
)
def test_the_limit_of_a_limit_hit_run_is_derived_in_order(usage: Usage, limit: str | None) -> None:
    assert limit_reached(usage, PLAN_VALUES) == limit


def test_an_empty_id_list_is_kept_in_the_store_answer() -> None:
    stored = stored_answer(answered(sources=({"issue": ("AAA-1",), "time_entry": ()},)))
    assert stored is not None and stored["full"]["sources"] == [
        {"issue": ["AAA-1"], "time_entry": []}
    ]


def test_a_large_store_answer_is_kept_as_its_hash_and_counts() -> None:
    ids = tuple(f"ISSUE-{n}" for n in range(5_000))
    stored = stored_answer(
        replace(
            answered(), shape="set", value=[f"issue:{i}" for i in ids], sources=({"issue": ids},)
        )
    )
    assert stored is not None and set(stored) == {"sha256", "value_items", "source_ids"}
    assert stored["value_items"] == 5_000 and stored["source_ids"] == [5_000]


# --- Run files (AC-29, AC-31, AC-37) ---


def test_a_run_file_is_written_once(tmp_path: Path) -> None:
    path = tmp_path / "v1/Q01-sql-1.json"
    write_once(path, {"run": 1})
    with pytest.raises(EvalError, match="written once"):
        write_once(path, {"run": 2})
    assert json.loads(path.read_text()) == {"run": 1}


def test_a_failed_write_leaves_no_file_at_all(tmp_path: Path) -> None:
    path = tmp_path / "v1/Q01-sql-1.json"
    with pytest.raises(TypeError):
        write_once(path, {"bad": object()})
    assert list((tmp_path / "v1").iterdir()) == []


def test_a_resume_plans_only_the_missing_run_numbers(root: Path) -> None:
    questions = read_questions(root)
    write_once(run_path(root, "v1", "Q01", "sql", 2), {"run": 2})
    planned = planned_work(root, "v1", questions, ("sql", "graph"), 3)
    assert [(w.store, w.number) for w in planned] == [
        ("sql", 1),
        ("sql", 3),
        ("graph", 1),
        ("graph", 2),
        ("graph", 3),
    ]


def test_runs_are_scored_written_and_hold_every_record_field(root: Path) -> None:
    written, spent, stopped = execute(root, StubAgent(), work(root, 1, 2))
    assert (written, stopped) == (2, [])
    assert spent == pytest.approx(0.02)
    record = read_run_files(root, "v1")[0]
    assert record["outcome"] == "correct" and record["correct"] is True
    assert record["load_id"] == LOAD_ID and record["response_model"] == "stub-model"
    assert (record["type"], record["data"], record["zero_answer"]) == ("aggregate", "real", False)
    assert record["attempt_count"] == 1 and record["retries"] == 0
    assert set(record["git"]) == {"commit", "dirty"} and len(record["compare_sha256"]) == 64


def test_an_outage_stops_with_how_to_resume_and_keeps_finished_files(root: Path) -> None:
    for outage in (ProviderOutage("HTTP 529 after retries"), StoreOutage("lost the connection")):
        agent = StubAgent(script=(answered(), outage))
        items = work(root, 1, 2, store="graph" if isinstance(outage, StoreOutage) else "sql")
        with pytest.raises(EvalError, match="run the same command again to fill only the missing"):
            execute(root, agent, items)
    assert len(read_run_files(root, "v1")) == 2


def test_the_cap_stops_starting_runs(root: Path) -> None:
    written, spent, stopped = execute(root, StubAgent(), work(root, 1, 2, 3), cap=0.015)
    assert written == 2 and spent == pytest.approx(0.02)
    assert stopped == ["stopped: the cost so far, $0.0200, reached the cap $0.0150"]


def test_files_on_another_load_id_are_named() -> None:
    files = [{"load_id": LOAD_ID}, {"load_id": "1761000000.1"}]
    assert load_id_problems(files, LOAD_ID) == (
        f"run files of this version hold load ID 1761000000.1, the current load ID is {LOAD_ID}",
    )


# --- The plan and the cost gate (AC-30) ---


def test_estimate_refuses_while_a_limit_is_unset() -> None:
    with pytest.raises(EvalError, match="ORGOS_AGENT_TOKEN_LIMIT"):
        make_plan(
            settings(ORGOS_AGENT_REQUEST_LIMIT=10, ORGOS_AGENT_TOOL_CALL_LIMIT=8), DESCRIPTION
        )


def test_estimate_writes_the_plan_once_with_its_upper_bound(root: Path) -> None:
    lines = estimate(settings(**LIMITS), root, "tests.test_eval_runs:stub_factory", only="Q01")
    plan = json.loads((root / "results/agent_runs/v1" / PLAN).read_text())
    # 20 questions, 3 runs, 2 stores, 50,000 tokens, $15 per million.
    assert plan["estimate_usd"] == pytest.approx(120 * 50_000 * 15 / 1_000_000)
    assert (plan["runs"], plan["token_limit"], plan["model_id"]) == (
        3,
        50_000,
        DESCRIPTION.model_id,
    )
    assert lines[-1] == "  upper bound for Q01 only: $4.50"
    with pytest.raises(EvalError, match="a written plan is not rewritten"):
        estimate(settings(**LIMITS), root, "tests.test_eval_runs:stub_factory", only=None)


@pytest.mark.parametrize(
    ("go", "cap", "costs", "to_start", "reason"),
    [
        (False, 5.0, [], 1, "only with --go"),
        (True, None, [], 1, "only with --max-cost-usd"),
        (True, 5.0, [], 2, "no finished run file yet"),
        (True, 1.0, [0.2, 0.4], 4, "times 4 runs is $1.2000, above the cap $1.0000"),
    ],
)
def test_the_gate_refuses(
    go: bool, cap: float | None, costs: list[float], to_start: int, reason: str
) -> None:
    problem = gate_problem(go=go, cap=cap, finished_costs=costs, to_start=to_start)
    assert problem is not None and reason in problem


@pytest.mark.parametrize(("costs", "to_start"), [([], 1), ([0.2, 0.4], 3)])
def test_the_gate_lets_a_run_through(costs: list[float], to_start: int) -> None:
    assert gate_problem(go=True, cap=1.0, finished_costs=costs, to_start=to_start) is None


# --- The versions log (AC-32) ---

FIRST = {"version_hash": "v1", "parts": dict(DESCRIPTION.parts)}


def test_the_first_version_needs_no_reason() -> None:
    entry = version_entry(
        [], DESCRIPTION, reason=None, exposed_by=None, kind=None, exposed_exists=False
    )
    assert entry["version_hash"] == "v1" and "changed" not in entry


def test_a_later_version_holds_its_changed_parts_and_reason() -> None:
    second = replace(DESCRIPTION, version_hash="v2", parts={**DESCRIPTION.parts, "tools": "c" * 64})
    entry = version_entry(
        [FIRST],
        second,
        reason="row cap text",
        exposed_by="r.json",
        kind="bug_fix",
        exposed_exists=True,
    )
    assert entry["changed"] == ["tools"] and entry["kind"] == "bug_fix"


@pytest.mark.parametrize(
    ("entries", "parts", "kind", "reason"),
    [
        (
            [FIRST, {**FIRST, "version_hash": "x"}, {**FIRST, "version_hash": "y"}],
            None,
            "bug_fix",
            "a fourth is refused",
        ),
        ([FIRST], dict(DESCRIPTION.parts), "bug_fix", "no part changed"),
        (
            [FIRST],
            {**DESCRIPTION.parts, "instructions": "d" * 64},
            "bug_fix",
            "behaviour change, not a bug fix",
        ),
        (
            [FIRST],
            {**DESCRIPTION.parts, "tools": "d" * 64},
            "tweak",
            "--kind bug_fix or behaviour_change",
        ),
    ],
)
def test_the_versions_log_refuses(
    entries: list[dict[str, Any]], parts: dict[str, str] | None, kind: str, reason: str
) -> None:
    second = replace(DESCRIPTION, version_hash="v2", parts=parts or DESCRIPTION.parts)
    with pytest.raises(EvalError, match=reason):
        version_entry(
            entries, second, reason="r", exposed_by="r.json", kind=kind, exposed_exists=True
        )


def test_version_add_logs_the_loaded_agent(root: Path) -> None:
    line = version_add(
        settings(),
        root,
        "tests.test_eval_runs:stub_factory",
        reason=None,
        exposed_by=None,
        kind=None,
    )
    assert line.startswith("logged version v1 (1 of 3)")
    assert json.loads((root / VERSIONS).read_text())["parts"] == dict(DESCRIPTION.parts)


# --- Agent loading (AC-38) ---


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        ("stub_factory", "replay or module:factory"),
        ("tests.no_such_module:factory", "cannot load the agent"),
        ("tests.test_eval_runs:not_an_agent", "did not return a QueryAgent"),
    ],
)
def test_a_bad_agent_path_is_refused(root: Path, spec: str, reason: str) -> None:
    with pytest.raises(EvalError, match=reason):
        load_agent(spec, settings(), root)


def test_replay_is_exempt_from_the_plan_and_the_versions_log(root: Path) -> None:
    with pytest.raises(EvalError, match="exempt from the plan"):
        estimate(settings(**LIMITS), root, "replay", only=None)
    with pytest.raises(EvalError, match="exempt from the versions log"):
        version_add(settings(), root, "replay", reason=None, exposed_by=None, kind=None)


# --- The summary (AC-23, AC-33, AC-34) ---


def entry(layer: str, store: str, question: str, outcome: str, **extra: Any) -> Entry:
    return Entry(
        layer,
        "v1" if layer == "agent" else layer,
        store,
        question,
        "aggregate",
        "real",
        outcome,
        **extra,
    )  # type: ignore[arg-type]


def test_the_summary_shows_raw_counts_with_and_without_the_zero_guarded() -> None:
    entries = [
        entry("gold", "sql", "Q01", "correct"),
        entry("gold", "sql", "Q03", "correct"),
        entry("gold", "sql", "Q05", "discrepancy"),
        entry("gold", "sql", "Q06", "not_tried", timeouts=2),
    ]
    lines = summary_lines(entries, frozenset({"Q03", "Q16"}))
    assert lines[0] == "gold sql: 2/4 correct (1/3 without the zero guarded Q03, Q16)"
    assert lines[1] == "  discrepancies 1; attempts timed out 2, at the row cap 0"
    assert lines[3] == "  by data: real 2/4, synthetic 0/0"


def test_replay_is_its_own_layer_and_never_partial() -> None:
    entries = [
        entry("replay", "sql", "Q01", "correct", attempts=2),
        entry("agent", "sql", "Q01", "correct", attempts=3, retries=1),
        entry("agent", "sql", "Q01", "not_tried", missing="Q01-sql-2"),
    ]
    found = groups(entries, frozenset())
    assert [(g.layer, g.record()["headline"]) for g in found] == [
        ("agent", {"correct": 1, "total": 2}),
        ("replay", {"correct": 1, "total": 1}),
    ]
    assert found[0].record()["partial"] and not found[1].record()["partial"]
    assert "  partial, missing: Q01-sql-2" in found[0].lines()
    assert "  Q01: 2 runs, 3 attempts, 1 retries" in found[0].lines()


def test_planned_runs_without_a_file_stay_in_the_denominator(root: Path) -> None:
    execute(root, StubAgent(), work(root, 1))
    files = read_run_files(root, "v1")
    entries = run_entries(files, "v1", read_questions(root), 3)
    assert len(entries) == 6
    assert sorted(e.missing for e in entries if e.missing) == [
        "Q01-graph-1",
        "Q01-graph-2",
        "Q01-graph-3",
        "Q01-sql-2",
        "Q01-sql-3",
    ]


def test_versions_that_disagree_on_graph_versus_sql_are_named() -> None:
    def version(name: str, sql: str, graph: str) -> list[Entry]:
        return [
            replace(entry("agent", "sql", "Q01", sql), version=name),
            replace(entry("agent", "graph", "Q01", graph), version=name),
        ]

    agree = groups(
        [*version("v1", "correct", "wrong"), *version("v2", "correct", "wrong")], frozenset()
    )
    assert versions_statement(agree) is None
    differ = groups(
        [*version("v1", "correct", "wrong"), *version("v2", "wrong", "correct")], frozenset()
    )
    assert versions_statement(differ) == (
        "The agent versions disagree on graph versus SQL: v1 SQL ahead; v2 graph ahead."
    )


# --- Replay (AC-34) and the gold summary (AC-13) ---


def test_replay_refuses_a_question_with_no_accepted_pair(root: Path) -> None:
    with pytest.raises(EvalError, match="has no accepted gold pair; replay refuses it"):
        accepted_files(root, "Q01", "sql")


def test_replay_reads_the_pinned_files_in_step_order_and_refuses_an_edit(root: Path) -> None:
    folder = root / "gold/sql"
    folder.mkdir(parents=True)
    names = ("Q01.answer.sql", "Q01.sources.2.sql", "Q01.sources.1.sql", "Q01.explain.md")
    for name in names:
        (folder / name).write_text(f"-- {name}\n")
    (root / "gold/ACCEPTED").write_text(
        "".join(f"{sha256_file(folder / name)}  gold/sql/{name}\n" for name in names)
    )
    assert [str(path) for path in accepted_files(root, "Q01", "sql")] == [
        "gold/sql/Q01.answer.sql",
        "gold/sql/Q01.sources.1.sql",
        "gold/sql/Q01.sources.2.sql",
    ]
    (folder / "Q01.sources.1.sql").write_text("-- edited\n")
    with pytest.raises(EvalError, match="differs from its pin"):
        accepted_files(root, "Q01", "sql")


def test_gold_entries_take_the_latest_state_of_every_pair(root: Path) -> None:
    questions = read_questions(root)
    lines = (
        {
            "event": "try",
            "question": "Q01",
            "store": "sql",
            "round": 1,
            "try": 1,
            "correct": False,
            "outcome": "store_timeout",
        },
        {
            "event": "try",
            "question": "Q01",
            "store": "sql",
            "round": 1,
            "try": 2,
            "correct": True,
            "outcome": "correct",
        },
        {
            "event": "try",
            "question": "Q01",
            "store": "graph",
            "round": 1,
            "try": 1,
            "correct": False,
            "outcome": "literal",
        },
    )
    entries = {e.store: e for e in gold_entries(lines, questions)}
    assert entries["sql"].outcome == "correct" and entries["sql"].timeouts == 1
    assert entries["graph"].outcome == "error"
