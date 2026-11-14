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
    run_path,
    run_plain_agent,
    run_work,
    version_dir,
    write_once,
    write_plan,
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
    write_once(run_path(noted, "Q01", "sql", 1), {"run": 1})
    (noted / "summary.json").write_text("[]\n")
    before = snapshot(noted)
    agent = PlainStub()
    use(monkeypatch, agent)
    plan = estimated(root)
    lines = run_plain(root)
    assert "wrote 1 run files under results/agent_runs/v1/plain/" in lines
    assert agent.plain_calls == ["Q01-sql"] and agent.calls == ["Q01"]
    assert snapshot(noted) == before
    assert read_run_files(noted) == ({"run": 1},)
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
