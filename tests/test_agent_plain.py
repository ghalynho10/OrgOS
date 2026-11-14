"""The plain run block, `run_plain` and the plain block pin (spec 0013 AC-1 to AC-8).

The blocks are read from the 20 questions as the runner reads them, amendments
applied, and never from a known answer. The agent runs on a `FunctionModel`
with fake executors, so nothing reaches a model or a store (AC-28).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo

from orgos.agent.agent import (
    blocks_sha256,
    read_layouts,
    read_plain_blocks,
    read_question_blocks,
)
from orgos.agent.prompt import render_plain_block, render_plain_run_block, render_question_block
from orgos.eval.answers import read_questions
from orgos.eval.frozen import AMENDMENTS
from orgos.eval.handoff import AgentDescription, AgentRun, PlainQueryAgent, QueryAgent, Store
from orgos.eval.models import Question, read_toml
from orgos.eval.replay import ReplayAgent
from orgos.eval.runs import RUNS_DIR, VERSIONS
from tests.test_agent_run import (
    QUESTION,
    SET_ANSWER,
    Script,
    _agent,
    _feedback,
    _layout_sends,
    _offline_settings,
    _scripted,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = read_questions(ROOT)
LAYOUTS = read_layouts(QUESTIONS)
# The plain blocks of the 20 questions, fixed before the first plain run (AC-8). A change
# to the renderer or to a question breaks this test: edit the value only on purpose.
PLAIN_BLOCKS_SHA256 = "f4f986263ba5e530d0331e7451b4c5d129609e0522e773d21319875172f64e23"
CURRENT_VERSION = "cae2a6746caf3d715a7b065d3abdc5b7f3abcce5e9df0620956827860e03c48e"


@pytest.fixture(autouse=True)
def no_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any real model request in this file fails at once (spec 0013 AC-28)."""
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)


def _scored(question: Question) -> str:
    return render_question_block(question, LAYOUTS[question.id])


# --- The plain block (AC-1, AC-2) ---


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_the_plain_block_is_the_scored_block_cut_before_its_note(question: Question) -> None:
    scored = _scored(question)
    assert "\nReading note:\n" in scored
    assert render_plain_run_block(question) == scored.split("\nReading note:\n", 1)[0]


def test_the_amended_q07_is_the_one_tested() -> None:
    amended = {a["question"]: a["after"] for a in read_toml(ROOT / AMENDMENTS)["amendment"]}
    q07 = next(question for question in QUESTIONS if question.id == "Q07")
    assert q07 == Question.model_validate(amended["Q07"])


def test_only_q08_and_q09_print_a_hop_limit() -> None:
    with_limit = [q.id for q in QUESTIONS if "Hop limit:" in render_plain_run_block(q)]
    assert with_limit == ["Q08", "Q09"]


def test_the_plain_block_holds_no_note_and_no_hidden_field() -> None:
    question = QUESTIONS[0].model_copy(
        update={"fields": ("fields.marker-path",), "reading": "MARKER NOTE LINE\nIf empty: x"}
    )
    block = render_plain_run_block(question)
    assert "Reading note:" not in block
    assert "MARKER" not in block
    assert "If empty:" not in block
    assert "Sources layout" not in block
    assert "fields.marker-path" not in block
    for word in ("Type:", "Hops:", "Data:", "Tracer:", "Fields:", "aggregate", "real", "True"):
        assert word not in block


def test_both_stores_get_the_same_plain_block() -> None:
    blocks: dict[Store, list[str]] = {"sql": [], "graph": []}

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        store: Store = "graph" if "Neo4j" in (info.instructions or "") else "sql"
        blocks[store].extend(
            str(part.content) for part in first.parts if part.part_kind == "user-prompt"
        )
        return ModelResponse(parts=[TextPart("cannot say")])

    agent = _agent(script)
    for question in QUESTIONS:
        agent.run_plain(question, "sql")
        agent.run_plain(question, "graph")
    assert blocks["sql"] == blocks["graph"] == [render_plain_run_block(q) for q in QUESTIONS]


# --- The version hash is untouched (AC-3, AC-5) ---


def test_the_question_blocks_part_is_the_one_logged_for_the_current_version() -> None:
    entries = [
        json.loads(line) for line in (ROOT / VERSIONS).read_text(encoding="utf-8").splitlines()
    ]
    current = next(e for e in entries if e["version_hash"] == CURRENT_VERSION)
    agent_order = tuple(sorted(QUESTIONS, key=lambda q: q.id))
    computed = blocks_sha256(read_question_blocks(agent_order, LAYOUTS))
    assert computed == current["parts"]["question_blocks"]


def test_ask_and_its_block_are_unchanged() -> None:
    assert render_plain_block("How many projects?") == "Question: How many projects?"
    prompts: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.extend(str(part.content) for part in first.parts if part.part_kind == "user-prompt")
        return ModelResponse(parts=[TextPart("cannot say")])

    _agent(script).ask("Which issues exist?", "sql")
    assert prompts == ["Question: Which issues exist?"]


# --- run_plain (AC-3, AC-4) ---


def _capture(prompts: list[str], instructions: list[str | None], inner: Script) -> Script:
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        if len(messages) == 1:
            prompts.extend(
                str(part.content) for part in first.parts if part.part_kind == "user-prompt"
            )
            instructions.append(info.instructions)
        return inner(messages, info)

    return script


@pytest.mark.parametrize("store", ["sql", "graph"])
def test_run_plain_sends_the_instructions_of_run_and_the_plain_block(store: Store) -> None:
    plain_prompts: list[str] = []
    plain_instructions: list[str | None] = []
    scored_prompts: list[str] = []
    scored_instructions: list[str | None] = []
    plain = _capture(plain_prompts, plain_instructions, _scripted("text:cannot say"))
    _agent(plain).run_plain(QUESTION, store)
    scored = _capture(scored_prompts, scored_instructions, _scripted("text:cannot say"))
    _agent(scored).run(QUESTION, store)
    assert plain_instructions == scored_instructions
    assert plain_prompts == [render_plain_run_block(QUESTION)]
    assert "Reading note:" not in plain_prompts[0]
    assert "Open means status Open." not in plain_prompts[0]
    assert "Sources layout" not in plain_prompts[0]
    assert scored_prompts[0].startswith(plain_prompts[0] + "\nReading note:\n")


def test_run_plain_sends_a_wrong_declared_shape_back() -> None:
    texts: list[str] = []
    wrong = {**SET_ANSWER, "shape": "number"}
    run = _agent(_feedback(texts, _scripted("SELECT 1", wrong, SET_ANSWER))).run_plain(
        QUESTION, "sql"
    )
    assert run.outcome == "answered"
    assert run.shape == "set"
    assert any("not accepted yet" in text for text in texts)


def test_run_plain_has_no_layout_check() -> None:
    texts: list[str] = []
    wrong_layout = {**SET_ANSWER, "sources": [{"person": {"result": "r1", "column": "id"}}]}
    run = _agent(_feedback(texts, _scripted("SELECT 1", wrong_layout))).run_plain(QUESTION, "sql")
    assert run.outcome == "answered"
    assert run.usage.requests == 2
    assert _layout_sends(texts) == []


def test_run_plain_needs_no_layout_entry() -> None:
    other = QUESTION.model_copy(update={"id": "Q02"})
    run = _agent(_scripted("text:cannot say")).run_plain(other, "sql")
    assert run.outcome == "no_answer"


# --- The protocol and the block pin (AC-6 to AC-8) ---


@dataclass(frozen=True)
class _Plain:
    def describe(self) -> AgentDescription:
        raise NotImplementedError

    def run(self, question: Question, store: Store) -> AgentRun:
        raise NotImplementedError

    def run_plain(self, question: Question, store: Store) -> AgentRun:
        raise NotImplementedError

    def plain_blocks_sha256(self, questions: object) -> str:
        return "x"


@dataclass(frozen=True)
class _NotedOnly:
    def describe(self) -> AgentDescription:
        raise NotImplementedError

    def run(self, question: Question, store: Store) -> AgentRun:
        raise NotImplementedError


@dataclass(frozen=True)
class _NoDescribe:
    def run(self, question: Question, store: Store) -> AgentRun:
        raise NotImplementedError

    def run_plain(self, question: Question, store: Store) -> AgentRun:
        raise NotImplementedError

    def plain_blocks_sha256(self, questions: object) -> str:
        return "x"


def test_the_plain_protocol_takes_only_an_agent_with_both_modes() -> None:
    assert isinstance(_Plain(), PlainQueryAgent)
    assert isinstance(_agent(_scripted()), PlainQueryAgent)
    assert not isinstance(_NotedOnly(), PlainQueryAgent)
    assert not isinstance(_NoDescribe(), PlainQueryAgent)
    replay = ReplayAgent(_offline_settings(), ROOT / RUNS_DIR / "unused")
    assert not isinstance(replay, PlainQueryAgent)
    assert PlainQueryAgent.__mro__[1] is QueryAgent


def test_the_plain_blocks_are_pinned() -> None:
    agent = _agent(_scripted())
    assert agent.plain_blocks_sha256(QUESTIONS) == PLAIN_BLOCKS_SHA256
    assert blocks_sha256(read_plain_blocks(QUESTIONS)) == PLAIN_BLOCKS_SHA256


def test_the_pin_does_not_depend_on_the_order_passed_in() -> None:
    agent = _agent(_scripted())
    assert agent.plain_blocks_sha256(tuple(reversed(QUESTIONS))) == PLAIN_BLOCKS_SHA256


def test_the_pin_hashes_like_the_question_blocks_part() -> None:
    blocks = read_plain_blocks(QUESTIONS)
    agent_order = tuple(sorted(QUESTIONS, key=lambda q: q.id))
    assert blocks == tuple(render_plain_run_block(q) for q in agent_order)
    changed = (*blocks[:-1], blocks[-1] + " ")
    assert blocks_sha256(changed) != PLAIN_BLOCKS_SHA256
