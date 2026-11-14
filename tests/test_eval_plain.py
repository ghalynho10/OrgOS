"""The plain series of a version with a stub `PlainQueryAgent` and no store (spec 0013).

The runner is driven on tmp folders: the preflight is replaced by a fixed load
ID, the agent by a stub that never calls a model (AC-28), and the questions and
known answer are the one question fixture of `tests.test_eval_runs`.
"""

import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orgos.config import Settings, load_settings
from orgos.eval import EvalError
from orgos.eval.answers import read_questions
from orgos.eval.frozen import sha256_file
from orgos.eval.gold import COMPARE_CODE
from orgos.eval.handoff import AgentRun, PlainQueryAgent, QueryAttempt, Store, Usage
from orgos.eval.models import Question
from orgos.eval.runs import (
    PLAN,
    VERSIONS,
    PlainPlan,
    Plan,
    Work,
    estimate_plain,
    load_plain_agent,
    plain_dir,
    plan_record,
    read_plain_plan,
    read_run_files,
    run_entries,
    run_path,
    run_plain_agent,
    run_work,
    score_run,
    version_dir,
    write_once,
    write_plan,
)
from orgos.eval.score import (
    Entry,
    groups,
    plain_statement,
    plain_statement_lines,
    summary_lines,
    versions_statement,
)
from tests.test_eval_gold import KNOWN_MAP
from tests.test_eval_runs import (
    DESCRIPTION,
    LIMITS,
    LOAD_ID,
    StubAgent,
    answered,
    make_root,
)

BLOCKS = "b" * 64
NOTED_PLAN = Plan(2, 10, 50_000, 8, 30, DESCRIPTION.model_id, "t", 3.0, 15.0, 1.0)


@dataclass(frozen=True)
class PlainStub(StubAgent):
    """A `PlainQueryAgent` whose plain mode answers from the script; `run` is never called."""

    blocks: str = BLOCKS
    # Each stub keeps its own records; the lists are mutated, the fields never reassigned.
    plain_calls: list[str] = field(default_factory=list)
    hashed: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, question: Question, store: Store) -> AgentRun:
        raise AssertionError("a plain command never calls run")

    def run_plain(self, question: Question, store: Store) -> AgentRun:
        self.plain_calls.append(f"{question.id}-{store}")
        return StubAgent.run(self, question, store)

    def plain_blocks_sha256(self, questions: Sequence[Question]) -> str:
        self.hashed.append(tuple(question.id for question in questions))
        return self.blocks


def plain_factory(settings: Settings) -> PlainStub:
    return PlainStub()


def noted_factory(settings: Settings) -> StubAgent:
    return StubAgent()


def settings(**overrides: object) -> Settings:
    return load_settings(
        _env_file=None,
        POSTGRES_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_OWNER_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_READER_PASSWORD=secrets.token_hex(8),
        MONGO_INITDB_ROOT_PASSWORD=secrets.token_hex(8),
        ORGOS_MONGO_READER_PASSWORD=secrets.token_hex(8),
        NEO4J_AUTH=f"neo4j/{secrets.token_hex(8)}",
        **LIMITS,
        **overrides,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A tmp root whose version v1 is logged and has its noted plan."""
    made = make_root(tmp_path)
    (made / VERSIONS).parent.mkdir(parents=True)
    (made / VERSIONS).write_text(json.dumps({"version_hash": "v1", "parts": {}}) + "\n")
    write_plan(made, version_dir(made, "v1"), NOTED_PLAN)
    return made


def estimated(root: Path) -> PlainPlan:
    estimate_plain(settings(), root, "tests.test_eval_plain:plain_factory", only=None)
    plan = read_plain_plan(plain_dir(root, "v1"))
    assert plan is not None
    return plan


def use(monkeypatch: pytest.MonkeyPatch, agent: object, load_id: str = LOAD_ID) -> list[str]:
    """Replace the preflight and the agent loader; return the preflight calls."""
    calls: list[str] = []

    def checked(settings: Settings, root: Path) -> SimpleNamespace:
        calls.append("preflight")
        return SimpleNamespace(load_id=load_id)

    monkeypatch.setattr("orgos.eval.runs.preflight", checked)
    monkeypatch.setattr("orgos.eval.runs.load_agent", lambda spec, s, r: agent)
    return calls


def run_plain(root: Path, **options: Any) -> tuple[str, ...]:
    chosen: dict[str, Any] = {
        "go": True,
        "max_cost_usd": 1.0,
        "only": "Q01",
        "store": "sql",
        "max_run": 1,
    } | options
    return run_plain_agent(settings(), root, "tests.test_eval_plain:plain_factory", **chosen)


def noted_file(
    number: int,
    *,
    store: Store = "sql",
    cost: float = 0.01,
    answer_match: bool = True,
    load_id: str = LOAD_ID,
    question: str = "Q01",
) -> dict[str, Any]:
    """The top level fields of a noted run file that the plain command reads."""
    return {
        "question": question,
        "store": store,
        "run": number,
        "agent_version": "v1",
        "load_id": load_id,
        "answer_match": answer_match,
        "usage": {"cost_usd": cost, "requests": 2},
    }


def snapshot(folder: Path) -> Mapping[str, bytes]:
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


# --- The agent of a plain command (AC-6, AC-9) ---


def test_replay_and_an_agent_with_no_plain_mode_are_refused(root: Path) -> None:
    with pytest.raises(EvalError, match="replay has no plain mode"):
        load_plain_agent("replay", settings(), root)
    with pytest.raises(EvalError, match="has no plain mode: it is not a PlainQueryAgent"):
        load_plain_agent("tests.test_eval_plain:noted_factory", settings(), root)
    agent = load_plain_agent("tests.test_eval_plain:plain_factory", settings(), root)
    assert isinstance(agent, PlainQueryAgent)


def test_estimate_and_run_refuse_replay(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(EvalError, match="replay has no plain mode"):
        estimate_plain(settings(), root, "replay", only=None)
    use(monkeypatch, PlainStub())
    with pytest.raises(EvalError, match="replay has no plain mode"):
        run_plain_agent(
            settings(), root, "replay", go=True, max_cost_usd=1.0, only=None, store=None, max_run=1
        )


# --- The versions log and the plan (AC-7, AC-10, AC-13) ---


def test_an_unlogged_version_is_refused_and_no_entry_is_added(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / VERSIONS).write_text(json.dumps({"version_hash": "v0", "parts": {}}) + "\n")
    before = (root / VERSIONS).read_bytes()
    with pytest.raises(EvalError, match=f"version v1 has no entry in {VERSIONS}"):
        estimate_plain(settings(), root, "tests.test_eval_plain:plain_factory", only=None)
    use(monkeypatch, PlainStub())
    with pytest.raises(EvalError, match=f"version v1 has no entry in {VERSIONS}"):
        run_plain(root)
    assert (root / VERSIONS).read_bytes() == before
    assert not plain_dir(root, "v1").exists()


def test_estimate_needs_the_noted_plan(root: Path) -> None:
    (version_dir(root, "v1") / PLAN).unlink()
    with pytest.raises(
        EvalError, match=r"has no PLAN\.json; run `orgos eval run --estimate` first"
    ):
        estimate_plain(settings(), root, "tests.test_eval_plain:plain_factory", only=None)
    assert not plain_dir(root, "v1").exists()


def test_the_plain_plan_copies_runs_and_is_one_flat_object(root: Path) -> None:
    lines = estimate_plain(settings(), root, "tests.test_eval_plain:plain_factory", only="Q01")
    assert lines[0] == "wrote results/agent_runs/v1/plain/PLAN.json for version v1, series plain"
    assert lines[1] == f"  headline answer_match; plain blocks sha256 {BLOCKS}"
    flat = json.loads((plain_dir(root, "v1") / PLAN).read_text())
    # The settings say 3 runs; the noted plan says 2, and the plain plan copies it.
    assert settings().eval_runs == 3 and flat["runs"] == 2
    # 20 questions, 2 runs, 2 stores, 50,000 tokens, $15 per million.
    assert flat["estimate_usd"] == pytest.approx(80 * 50_000 * 15 / 1_000_000)
    assert (flat["version_hash"], flat["series"], flat["headline"]) == (
        "v1",
        "plain",
        "answer_match",
    )
    assert flat["plain_blocks_sha256"] == BLOCKS
    assert all(not isinstance(value, dict) for value in flat.values())
    plan = read_plain_plan(plain_dir(root, "v1"))
    assert plan is not None and plan_record(plan) == flat
    assert (plan.plan.model_id, plan.plan.price_table, plan.plan.token_limit) == (
        DESCRIPTION.model_id,
        DESCRIPTION.price_table,
        50_000,
    )
    with pytest.raises(EvalError, match="a written plan is not rewritten"):
        estimate_plain(settings(), root, "tests.test_eval_plain:plain_factory", only=None)


# covers: AC-13, the limits and the upper bound come from Settings; only `runs` from the noted plan
def test_the_plain_plan_takes_its_limits_from_settings_and_only_runs_from_the_noted_plan(
    root: Path,
) -> None:
    lowered = settings().model_copy(update={"agent_token_limit": 25_000, "agent_request_limit": 6})
    estimate_plain(lowered, root, "tests.test_eval_plain:plain_factory", only=None)
    plan = read_plain_plan(plain_dir(root, "v1"))
    assert plan is not None
    assert (NOTED_PLAN.token_limit, NOTED_PLAN.request_limit) == (50_000, 10)
    assert (plan.plan.runs, plan.plan.token_limit, plan.plan.request_limit) == (2, 25_000, 6)
    # Half the token limit of the test above, so half its upper bound.
    assert plan.plan.estimate_usd == pytest.approx(80 * 25_000 * 15 / 1_000_000)


def test_the_block_hash_is_over_every_question_even_with_only(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = PlainStub()
    use(monkeypatch, agent)
    estimate_plain(settings(), root, "tests.test_eval_plain:plain_factory", only="Q01")
    run_plain(root, only="Q01")
    every = tuple(question.id for question in read_questions(root))
    assert agent.hashed == [every, every]


# --- The preflight (AC-11) ---


def test_estimate_runs_no_preflight_and_a_run_runs_it_first(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = use(monkeypatch, PlainStub())
    estimated(root)
    assert calls == []
    run_plain(root)
    assert calls == ["preflight"]


# --- Plain run files (AC-12, AC-14, AC-16 to AC-18) ---


def test_a_plain_run_writes_only_under_plain(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    noted = version_dir(root, "v1")
    write_once(run_path(noted, "Q01", "sql", 1), noted_file(1))
    (noted / "summary.json").write_text("[]\n")
    before = snapshot(noted)
    agent = PlainStub()
    use(monkeypatch, agent)
    plan = estimated(root)
    lines = run_plain(root)
    assert "wrote 1 run files under results/agent_runs/v1/plain/" in lines
    assert agent.plain_calls == ["Q01-sql"] and agent.calls == ["Q01"]
    assert snapshot(noted) == before
    assert read_run_files(noted) == (noted_file(1),)
    (record,) = read_run_files(plain_dir(root, "v1"))
    assert (record["series"], record["plain_blocks_sha256"]) == ("plain", BLOCKS)
    assert record["plan"] == plan_record(plan)
    assert (record["agent_version"], record["load_id"]) == ("v1", LOAD_ID)
    assert record["answer_match"] is True and record["outcome"] == "correct"
    assert len(record["compare_sha256"]) == 64


def test_a_plain_run_file_is_written_once(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, PlainStub())
    estimated(root)
    run_plain(root)
    lines = run_plain(root)
    assert "every planned plain run number already has a file; nothing to run" in lines
    with pytest.raises(EvalError, match="written once"):
        write_once(run_path(plain_dir(root, "v1"), "Q01", "sql", 1), {"run": 1})


def test_a_changed_block_hash_is_refused_naming_both(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    estimated(root)
    agent = PlainStub(blocks="c" * 64)
    use(monkeypatch, agent)
    with pytest.raises(EvalError) as raised:
        run_plain(root)
    assert f"the plain blocks hash is {'c' * 64}, the plain PLAN.json holds {BLOCKS}" in str(
        raised.value
    )
    assert agent.plain_calls == []


def test_a_run_needs_the_plain_plan(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, PlainStub())
    with pytest.raises(EvalError, match="run `orgos eval run --plain --estimate` first"):
        run_plain(root)


def test_a_plain_file_on_another_load_id_refuses(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, PlainStub())
    estimated(root)
    run_plain(root)
    agent = PlainStub()
    use(monkeypatch, agent, load_id="1763000000.1")
    with pytest.raises(
        EvalError, match=re.escape(f"hold load ID {LOAD_ID}, the current load ID is")
    ):
        run_plain(root, max_run=2)
    assert agent.plain_calls == []


def plain_work(root: Path, agent: PlainStub, plan: PlainPlan) -> tuple[int, float, list[str]]:
    question = read_questions(root)[0]
    return run_work(
        root,
        settings(),
        agent.run_plain,
        (Work(question, "sql", 1),),
        folder=plain_dir(root, "v1"),
        version="v1",
        plan=plan,
        load_id=LOAD_ID,
        known={"Q01": KNOWN_MAP},
        cap=None,
    )


def test_a_fallback_record_holds_the_series_fields(root: Path) -> None:
    plan = estimated(root)
    broken = replace(answered(), attempts=(QueryAttempt(object(), "ok", 0.1, 3),))  # type: ignore[arg-type]
    with pytest.raises(EvalError, match="failed after the model call"):
        plain_work(root, PlainStub(script=(broken,)), plan)
    (kept,) = read_run_files(plain_dir(root, "v1"))
    assert (kept["fallback"], kept["series"], kept["plain_blocks_sha256"]) == (
        True,
        "plain",
        BLOCKS,
    )


def test_the_limit_of_a_plain_limit_hit_run_comes_from_the_plain_plan(root: Path) -> None:
    plan = estimated(root)
    stopped = replace(
        answered(),
        outcome="limit_hit",
        shape=None,
        value=None,
        sources=(),
        usage=Usage(100, 10, plan.plan.request_limit, 2, 0.01),
    )
    plain_work(root, PlainStub(script=(stopped,)), plan)
    (record,) = read_run_files(plain_dir(root, "v1"))
    assert (record["outcome"], record["limit"], record["answer_match"]) == (
        "limit_hit",
        "requests",
        False,
    )


# --- The plain gate (AC-15) ---


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"go": False}, "only with --go"),
        ({"max_cost_usd": None}, "only with --max-cost-usd"),
        ({"store": None}, "the plain series of this version has no finished run file yet"),
        ({"max_run": 2}, "the plain series of this version has no finished run file yet"),
    ],
)
def test_the_first_plain_go_is_one_run_with_go_and_a_cap(
    root: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], reason: str
) -> None:
    agent = PlainStub()
    use(monkeypatch, agent)
    estimated(root)
    with pytest.raises(EvalError, match=reason):
        run_plain(root, **options)
    assert agent.plain_calls == []
    assert read_run_files(plain_dir(root, "v1")) == ()


def test_the_first_plain_go_is_limited_even_when_noted_runs_are_finished(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noted = version_dir(root, "v1")
    for number in (1, 2):
        write_once(run_path(noted, "Q01", "sql", number), noted_file(number))
    use(monkeypatch, PlainStub())
    estimated(root)
    with pytest.raises(EvalError, match="plain series of this version has no finished run file"):
        run_plain(root, store=None)


def test_the_gate_uses_the_plain_mean_and_never_the_noted_mean(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noted = version_dir(root, "v1")
    for number in (1, 2):
        write_once(run_path(noted, "Q01", "sql", number), noted_file(number, cost=9.0))
    agent = PlainStub()
    use(monkeypatch, agent)
    estimated(root)
    run_plain(root)
    # The plain mean is $0.01; 3 runs to start cost $0.03, under a $0.05 cap. The noted
    # mean, $9.00, would refuse.
    lines = run_plain(root, store=None, max_run=2, max_cost_usd=0.05)
    assert "measured mean cost of 1 finished runs: $0.0100" in lines
    assert len(read_run_files(plain_dir(root, "v1"))) == 4
    with pytest.raises(EvalError, match=r"times 2 runs is \$0\.0200, above the cap \$0\.0100"):
        run_plain(root, store=None, max_run=3, max_cost_usd=0.01)


def test_the_cap_stops_a_plain_invocation(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, PlainStub())
    estimated(root)
    run_plain(root)
    dear = replace(answered(), usage=replace(answered().usage, cost_usd=0.05))
    use(monkeypatch, PlainStub(script=(dear, dear)))
    # The gate allows 1 plain run ($0.01 mean) times 3 under $0.04; the first run costs $0.05.
    lines = run_plain(root, store=None, max_run=2, max_cost_usd=0.04)
    assert "stopped: the cost so far, $0.0500, reached the cap $0.0400" in lines
    assert len(read_run_files(plain_dir(root, "v1"))) == 2


def test_a_refresh_with_no_run_to_start_still_needs_go_and_a_cap(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, PlainStub())
    estimated(root)
    run_plain(root)
    for options, reason in (
        ({"go": False}, "only with --go"),
        ({"max_cost_usd": None}, "only with --max-cost-usd"),
    ):
        with pytest.raises(EvalError, match=reason):
            run_plain(root, **options)
    assert "every planned plain run number already has a file; nothing to run" in run_plain(root)


# --- The plain layer (AC-17, AC-19 to AC-22) ---

ZERO = frozenset({"Q03", "Q16"})


def plain(question: str, store: str, outcome: str, **extra: Any) -> Entry:
    kind = extra.pop("question_type", "aggregate")
    return Entry("plain", "v1", store, question, kind, "real", outcome, **extra)  # type: ignore[arg-type]


PLAIN_ENTRIES = (
    plain("Q01", "sql", "wrong", answer_match=True, source_match=False),
    plain("Q03", "sql", "correct", answer_match=True, source_match=True, timeouts=2),
    plain("Q05", "sql", "limit_hit", limit="tool_calls", row_caps=1),
    plain("Q09", "sql", "correct", answer_match=True, source_match=True, question_type="mixed"),
    plain("Q09", "sql", "not_tried", missing="Q09-sql-2", question_type="mixed"),
)


def test_the_plain_cells_count_answer_match_out_of_every_planned_run() -> None:
    (group,) = groups(PLAIN_ENTRIES, ZERO)
    record = group.record()
    assert record["headline"] == {"answer_match": 3, "total": 5}
    assert record["without_zero_guarded"] == {"answer_match": 2, "total": 4}
    assert record["by_type"]["aggregate"] == {"answer_match": 2, "total": 3}
    assert record["by_type"]["mixed"] == {"answer_match": 1, "total": 2}
    assert record["by_data"]["real"] == {"answer_match": 3, "total": 5}
    assert (record["source_match"], record["correct"]) == (2, 2)
    assert (record["attempts_timed_out"], record["attempts_at_row_cap"]) == (2, 1)
    assert record["limit_hit"] == {"requests": 0, "tool_calls": 1, "tokens": 0}
    assert record["partial"] and record["missing"] == ["Q09-sql-2"]


def test_the_plain_group_prints_lower_bound_and_the_counts_beside_it() -> None:
    (group,) = groups(PLAIN_ENTRIES, ZERO)
    lines = group.lines()
    assert lines[0] == (
        "plain v1 sql, lower bound: 3/5 answer_match (2/4 without the zero guarded Q03, Q16)"
        " PARTIAL"
    )
    assert lines[1] == (
        "  beside it: source_match 2, correct 2; attempts timed out 2, at the row cap 1"
    )
    assert "  limit_hit by limit: requests 0, tool_calls 1, tokens 0" in lines
    assert lines[-1] == "  partial, missing: Q09-sql-2"
    assert not any("seconds" in line or "latency" in line for line in lines)


def test_a_right_answer_with_no_sources_is_answer_match_without_source_match(root: Path) -> None:
    scored = score_run(KNOWN_MAP, answered(sources=()))
    assert (scored.answer_match, scored.source_match, scored.correct) == (True, False, False)
    record = {
        "question": "Q01",
        "store": "sql",
        "run": 1,
        "type": "aggregate",
        "data": "real",
        "outcome": scored.outcome,
        "attempt_count": 1,
        "retries": 0,
        "attempts": [],
        "limit": None,
        "answer_match": scored.answer_match,
        "source_match": scored.source_match,
    }
    entries = run_entries([record], "v1", read_questions(root), 2, series="plain")
    assert [e.layer for e in entries] == ["plain"] * 4
    (sql, graph) = (
        g.record()["headline"]
        for g in sorted(groups(entries, ZERO), key=lambda g: g.store != "sql")
    )
    assert sql == {"answer_match": 1, "total": 2} and graph == {"answer_match": 0, "total": 2}


def test_the_plain_layer_stays_out_of_the_noted_rules() -> None:
    noted = [
        Entry("agent", "v1", "sql", "Q01", "aggregate", "real", "correct"),
        Entry("agent", "v1", "graph", "Q01", "aggregate", "real", "wrong"),
    ]
    plain_side = [
        plain("Q01", "sql", "wrong", answer_match=False),
        plain("Q01", "graph", "correct", answer_match=True),
        plain("Q02", "graph", "not_tried", missing="Q02-graph-1"),
    ]
    found = groups([*noted, *plain_side], ZERO)
    assert versions_statement(found) is None
    agent = [g for g in found if g.layer == "agent"]
    assert not any(g.record()["partial"] for g in agent)
    assert all("source_match" not in g.record() for g in agent)
    # Noted lines do not depend on the new Entry fields.
    flipped = [replace(e, answer_match=True, source_match=True) for e in noted]
    assert summary_lines(noted, ZERO) == summary_lines(flipped, ZERO)


# --- The statement against noted (AC-23 to AC-25) ---


def run_file(question: str, store: str, number: int, match: bool, **extra: Any) -> dict[str, Any]:
    return {
        "question": question,
        "store": store,
        "run": number,
        "agent_version": "v1",
        "load_id": LOAD_ID,
        "answer_match": match,
    } | extra


def series(matches: Mapping[str, tuple[bool, bool]], store: str = "sql") -> list[dict[str, Any]]:
    return [
        run_file(question, store, number, match)
        for question, pair in matches.items()
        for number, match in enumerate(pair, start=1)
    ]


QIDS = ("Q01", "Q02", "Q03", "Q04", "Q05")


def statement(
    noted: list[dict[str, Any]],
    plain_files: list[dict[str, Any]],
    found: Sequence[Any] = (),
    versions: Sequence[str] = ("v1",),
) -> dict[str, Any]:
    return plain_statement(
        noted,
        plain_files,
        found,
        version="v1",
        question_ids=QIDS,
        runs=2,
        zero_guarded=ZERO,
        versions=versions,
    )


def test_the_statement_counts_both_series_over_the_compared_questions_only() -> None:
    noted = series({q: (True, True) for q in QIDS})
    plain_files = series({"Q01": (True, False), "Q02": (False, False), "Q03": (True, True)})
    plain_files += series({"Q04": (True, True), "Q05": (True, True)})
    # Q02: one noted file is missing; Q04: another load; Q05: another version.
    noted = [f for f in noted if (f["question"], f["run"]) != ("Q02", 2)]
    plain_files = [
        f | {"load_id": "1763000000.1"} if (f["question"], f["run"]) == ("Q04", 2) else f
        for f in plain_files
    ]
    plain_files = [
        f | {"agent_version": "v0"} if (f["question"], f["run"]) == ("Q05", 1) else f
        for f in plain_files
    ]
    # Noted `correct` disagrees with noted answer_match: it must never be read.
    noted = [f | {"correct": False} for f in noted]
    (sql, graph) = sorted(
        statement(noted, plain_files)["stores"], key=lambda p: p["store"] != "sql"
    )
    assert sql["compared"] == ["Q01", "Q03"]
    assert sql["not_compared"] == {
        "Q02": "missing files",
        "Q04": "other load",
        "Q05": "other version",
    }
    assert sql["full"] == {
        "plain": {"answer_match": 3, "total": 4},
        "noted": {"answer_match": 4, "total": 4},
    }
    assert sql["without_zero_guarded"] == {
        "plain": {"answer_match": 1, "total": 2},
        "noted": {"answer_match": 2, "total": 2},
    }
    assert graph["compared"] == [] and set(graph["not_compared"].values()) == {"missing files"}


def test_the_statement_lines_hold_counts_ids_and_lower_bound() -> None:
    noted = series({"Q01": (True, True), "Q02": (True, False)})
    plain_files = series({"Q01": (True, False), "Q02": (False, False)})
    lines = plain_statement_lines(statement(noted, plain_files))
    assert lines[1] == (
        "plain against noted SQL, version v1, lower bound: plain 1/4, noted 3/4 answer_match"
        " over 2 compared questions (plain 1/4, noted 3/4 without the zero guarded Q03, Q16);"
        " not compared: Q03 (missing files), Q04 (missing files), Q05 (missing files)"
    )
    assert all("lower bound" in line for line in lines)


def test_graph_against_sql_has_no_verdict_while_a_plain_series_is_partial() -> None:
    full = [
        plain("Q01", "sql", "correct", answer_match=True),
        plain("Q01", "graph", "wrong", answer_match=False),
    ]
    against = statement([], [], groups(full, ZERO))["graph_against_sql"]
    assert against == {
        "graph": {"answer_match": 0, "total": 1},
        "sql": {"answer_match": 1, "total": 1},
        "verdict": "SQL ahead",
    }
    partial = [*full, plain("Q02", "graph", "not_tried", missing="Q02-graph-1")]
    lines = plain_statement_lines(statement([], [], groups(partial, ZERO)))
    assert lines[2] == (
        "plain graph against SQL, lower bound: graph 0/2, SQL 1/1 answer_match;"
        " no verdict while a plain series is partial"
    )
    tied = [plain("Q01", s, "correct", answer_match=True) for s in ("sql", "graph")]
    assert statement([], [], groups(tied, ZERO))["graph_against_sql"]["verdict"] == "tied"


def test_a_later_version_makes_the_plain_series_stale() -> None:
    assert statement([], [], versions=("v0", "v1"))["stale"] == []
    stale = statement([], [], versions=("v1", "v2"))
    assert stale["stale"] == ["v2"]
    assert plain_statement_lines(stale)[-1] == (
        "the plain series of version v1 is stale, a lower bound of an older version:"
        " versions.jsonl holds the later v2; nothing is rerun"
    )


def test_a_plain_run_prints_and_stores_the_summary_and_the_statement(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noted = version_dir(root, "v1")
    for number in (1, 2):
        for store in ("sql", "graph"):
            write_once(run_path(noted, "Q01", store, number), noted_file(number, store=store))
    use(monkeypatch, PlainStub())
    estimated(root)
    run_plain(root)
    lines = run_plain(root, store=None, max_run=2)
    assert (
        "plain v1 sql, lower bound: 2/2 answer_match (2/2 without the zero guarded none)" in lines
    )
    assert any(
        line.startswith("plain against noted SQL, version v1, lower bound: plain 2/2,")
        for line in lines
    )
    assert "plain graph against SQL, lower bound: graph 2/2, SQL 2/2 answer_match; tied" in lines
    stored = json.loads((plain_dir(root, "v1") / "summary.json").read_text())
    assert [g["layer"] for g in stored["groups"]] == ["plain", "plain"]
    assert stored["statement"]["graph_against_sql"]["verdict"] == "tied"
    assert not (noted / "summary.json").exists()


# --- The command (AC-9, AC-11) ---


def cli(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> list[str]:
    from orgos.cli import app

    printed: list[str] = []
    monkeypatch.chdir(root)
    monkeypatch.setattr("orgos.cli.load_settings", settings)
    monkeypatch.setattr("typer.echo", printed.append)
    app(args=["eval", "run", *args], standalone_mode=False)
    return printed


def test_plain_estimate_on_the_command_line_runs_no_preflight(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_preflight(settings: Settings, root: Path) -> None:
        raise AssertionError("--estimate runs no preflight")

    monkeypatch.setattr("orgos.eval.runs.preflight", no_preflight)
    agent = "tests.test_eval_plain:plain_factory"
    printed = cli(root, monkeypatch, "--plain", "--estimate", "--agent", agent)
    assert printed[0] == "wrote results/agent_runs/v1/plain/PLAN.json for version v1, series plain"
    assert read_plain_plan(plain_dir(root, "v1")) is not None


def test_plain_go_on_the_command_line_runs_the_preflight_and_the_plain_mode(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = PlainStub()
    calls = use(monkeypatch, agent)
    estimated(root)
    options = ("--go", "--max-cost-usd", "1", "--only", "Q01", "--store", "graph", "--max-run", "1")
    printed = cli(root, monkeypatch, "--plain", "--agent", "x:y", *options)
    assert calls == ["preflight"] and agent.plain_calls == ["Q01-graph"]
    assert "wrote 1 run files under results/agent_runs/v1/plain/" in printed
    assert read_run_files(version_dir(root, "v1")) == ()


def test_plain_replay_on_the_command_line_is_refused(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(monkeypatch, PlainStub())
    with pytest.raises(EvalError, match="replay has no plain mode"):
        cli(root, monkeypatch, "--plain", "--agent", "replay", "--go", "--max-cost-usd", "1")
    with pytest.raises(EvalError, match="replay has no plain mode"):
        cli(root, monkeypatch, "--plain", "--estimate", "--agent", "replay")


# --- The guards (AC-26 to AC-28) ---

REPO = Path(__file__).resolve().parents[1]
# The comparison rule as merged on main; spec 0005 says it never changes after results.
COMPARE_SHA256 = "35f6b9f469e32bb54ba68664a5d3e84bda0f7a6f20b8f098c6d77cac489abc12"
PLANTED: dict[str, Any] = {
    "shape": "map",
    "value": [{"id": "project:PLANTZQ", "value": {"int": 987654321}}],
    "sources": [{"issue": ["PLANTZQ-77"]}],
}
PLANTED_TEXT = ("PLANTZQ", "987654321", "987,654,321", "987654320", "PLANTZQ-77")


@pytest.fixture(autouse=True)
def no_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any real model request in this file fails at once (AC-28)."""
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)


def test_the_comparison_rule_is_unchanged() -> None:
    assert sha256_file(REPO / COMPARE_CODE) == COMPARE_SHA256


def test_no_planted_known_value_reaches_the_output_or_the_log(
    root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    answers = {"amendments_sha256": None, "answers": {"Q01": PLANTED}, "questions_sha256": "x"}
    (root / "eval/answers.json").write_text(json.dumps(answers))
    right = answered(value=[("project:PLANTZQ", 987654321)], sources=({"issue": ("PLANTZQ-77",)},))
    wrong = answered(value=[("project:PLANTZQ", 987654320)], sources=({"issue": ("PLANTZQ-78",)},))
    empty = replace(answered(), outcome="no_answer", shape=None, value=None, sources=())
    broken = replace(right, attempts=(QueryAttempt(object(), "ok", 0.1, 3),))  # type: ignore[arg-type]
    caplog.set_level("DEBUG")
    printed = list(
        estimate_plain(settings(), root, "tests.test_eval_plain:plain_factory", only=None)
    )
    use(monkeypatch, PlainStub(script=(right,)))
    printed += run_plain(root)
    use(monkeypatch, PlainStub(script=(wrong, empty, right)))
    printed += run_plain(root, store=None, max_run=2)
    use(monkeypatch, PlainStub(script=(broken,)))
    with pytest.raises(EvalError) as raised:
        run_plain(root, max_run=3)
    printed.append(str(raised.value))
    shown = "\n".join([*printed, caplog.text, (plain_dir(root, "v1") / "summary.json").read_text()])
    assert "plain v1 sql, lower bound:" in shown and "failed after the model call" in shown
    for text in PLANTED_TEXT:
        assert text not in shown, text
