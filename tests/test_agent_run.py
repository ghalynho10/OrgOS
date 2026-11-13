"""The agent on a scripted `FunctionModel` and fake executors (spec 0012 AC-1 to AC-6, AC-16 to 24).

No test reaches a model or a store: the model is a Python function that answers
with tool calls and text, and each store is a function returning rows. The
prices here are made up for the arithmetic, never read from `prices.toml`.
"""

from __future__ import annotations

import ast
import datetime as dt
import inspect
import itertools
import logging
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelConnectionError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import orgos.agent
from orgos.agent.agent import Limits, OrgosQueryAgent, version_hash, version_parts
from orgos.agent.errors import AgentError, ProviderRefusal
from orgos.agent.meanings import Description
from orgos.agent.models import Prices
from orgos.agent.prompt import render_layout_line, visible_reading
from orgos.eval.handoff import ProviderOutage, Store, StoreOutage
from orgos.eval.models import Question
from orgos.sqlstore.query import QueryResult, SqlConnectionLost, SqlQueryTimeout

ROOT = Path(__file__).resolve().parents[1]
PRICES = Prices(
    page="test", date=dt.date(2026, 1, 1), input=2.0, output=10.0, cache_write=2.5, cache_read=0.2
)
LIMITS = Limits(
    request_limit=20,
    tool_call_limit=15,
    token_limit=400_000,
    max_tokens=16_000,
    max_rows=50,
    temperature=None,
    query_timeout_s=30,
)
DESCRIPTIONS = {
    "sql": Description(text="Schema description: sql test", keys=frozenset()),
    "graph": Description(text="Schema description: graph test", keys=frozenset()),
}
QUESTION = Question(
    id="Q01",
    text="How many issues are open?",
    type="aggregate",
    hops=0,
    hop_mode="fixed",
    shape="set",
    unit="none",
    fields=("fields.status.name",),
    reading="Open means status Open.\nIf empty: the answer is the empty set.",
    data="real",
)
LAYOUTS = {"Q01": (("issue",),)}
SIXTY = QueryResult(("id",), tuple({"id": f"CLOUD-{n}"} for n in range(60)))
SET_ANSWER = {
    "shape": "set",
    "answer_result": "r1",
    "id_kind": "issue",
    "sources": [{"issue": {"result": "r1", "column": "id"}}],
}

Script = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def _scripted(*steps: Any) -> Script:
    """A model that sends each step in turn: a statement, an answer dict, text or an error."""
    queue: Iterator[Any] = iter(steps)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = next(queue)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str) and step.startswith("text:"):
            return ModelResponse(parts=[TextPart(step.removeprefix("text:"))])
        if isinstance(step, str):
            return ModelResponse(parts=[ToolCallPart("run_query", {"statement": step})])
        return ModelResponse(parts=[ToolCallPart("final_answer", step)])

    return respond


def _executor(*results: QueryResult | Exception) -> Callable[[str], QueryResult]:
    queue = iter(results)

    def execute(statement: str) -> QueryResult:
        item = next(queue)
        if isinstance(item, Exception):
            raise item
        return item

    return execute


def _agent(
    script: Script,
    sql: Callable[[str], QueryResult] | None = None,
    limits: Limits = LIMITS,
    graph: Callable[[str], QueryResult] | None = None,
    model_id: str = "test:scripted",
    descriptions: dict[Store, Description] = DESCRIPTIONS,  # type: ignore[assignment]
    question_blocks: tuple[str, ...] = ("Question: one",),
    layouts: dict[str, tuple[tuple[str, ...], ...]] = LAYOUTS,
) -> OrgosQueryAgent:
    ticks = itertools.count(start=0.0, step=1.0)
    return OrgosQueryAgent(
        model=FunctionModel(script, model_name="scripted-model"),
        model_id=model_id,
        prices=PRICES,
        limits=limits,
        executors={"sql": sql or _executor(SIXTY), "graph": graph or _executor(SIXTY)},
        descriptions=descriptions,
        question_blocks=question_blocks,
        layouts=layouts,
        meanings_text="meanings",
        clock=lambda: next(ticks),
    )


# --- The contract (AC-1, AC-2) ---


def test_build_agent_is_loadable_by_the_runner_and_run_is_plain() -> None:
    assert orgos.agent.build_agent.__name__ == "build_agent"
    assert not inspect.iscoroutinefunction(OrgosQueryAgent.run)
    assert not inspect.iscoroutinefunction(OrgosQueryAgent.ask)


def test_no_provider_name_outside_models() -> None:
    names = ("anthropic", "openai", "NativeOutput", "Caching")
    for path in sorted((ROOT / "src/orgos/agent").glob("*.py")):
        if path.name == "models.py":
            continue
        text = path.read_text(encoding="utf-8").lower()
        for name in names:
            assert name.lower() not in text, (path.name, name)


def test_orgos_eval_never_imports_orgos_agent() -> None:
    for path in sorted((ROOT / "src/orgos/eval").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("orgos.agent"), path.name
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("orgos.agent") for a in node.names), path.name


def _offline_settings(**overrides: object) -> Any:
    from orgos.config import load_settings

    # Port 1 refuses at once: if a refusal below ever moved after a store read, the
    # test would fail with a store error instead of the named one.
    return load_settings(
        _env_file=None,
        POSTGRES_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_OWNER_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_READER_PASSWORD=secrets.token_hex(8),
        MONGO_INITDB_ROOT_PASSWORD=secrets.token_hex(8),
        ORGOS_MONGO_READER_PASSWORD=secrets.token_hex(8),
        NEO4J_AUTH=f"neo4j/{secrets.token_hex(8)}",
        ORGOS_PG_PORT=1,
        ORGOS_NEO4J_BOLT_PORT=1,
        **overrides,
    )


# covers: AC-20, `build_agent` raises ConfigError when ORGOS_MODEL is unset, before any store
def test_build_agent_refuses_without_a_model_before_any_store() -> None:
    from orgos.config import ConfigError

    with pytest.raises(ConfigError, match="ORGOS_MODEL not set"):
        orgos.agent.build_agent(_offline_settings(ORGOS_MODEL=None))


# covers: AC-23, a model with no price entry stops `build_agent` itself, before any store
def test_build_agent_refuses_an_unpriced_model_before_any_store() -> None:
    settings = _offline_settings(
        ORGOS_MODEL="anthropic:claude-not-priced", ANTHROPIC_API_KEY=secrets.token_hex(8)
    )
    with pytest.raises(AgentError, match="has no entry for 'anthropic:claude-not-priced'"):
        orgos.agent.build_agent(settings)


# --- One scripted run (AC-16, AC-18) ---


def test_a_scripted_run_answers_from_the_full_rows_not_the_preview() -> None:
    script = _scripted("SELECT issue_key AS id FROM model.issue", SET_ANSWER)
    run = _agent(script).run(QUESTION, "sql")
    assert run.outcome == "answered"
    assert run.shape == "set"
    assert len(run.value) == 60
    assert run.value[0] == "issue:CLOUD-0"
    assert run.value[-1] == "issue:CLOUD-59"
    assert run.sources == ({"issue": tuple(f"CLOUD-{n}" for n in range(60))},)
    assert [(a.outcome, a.rows) for a in run.attempts] == [("ok", 60)]
    assert run.usage.requests == 2
    assert run.usage.tool_calls == 1
    assert run.usage.input_tokens > 0
    assert run.response_model == "scripted-model"
    assert run.seconds == 1.0


def test_the_model_sees_the_instructions_the_block_and_no_if_empty_line() -> None:
    captured: list[AgentInfo] = []
    prompts: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.append(info)
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.extend(str(part.content) for part in first.parts if part.part_kind == "user-prompt")
        return ModelResponse(parts=[TextPart("cannot say")])

    _agent(script).run(QUESTION, "graph")
    info = captured[0]
    assert info.allow_text_output
    assert [tool.name for tool in info.function_tools] == ["run_query"]
    assert [tool.name for tool in info.output_tools] == ["final_answer"]
    assert "Neo4j (Cypher 25)" in (info.instructions or "")
    assert "Schema description: graph test" in (info.instructions or "")
    assert "Schema description: sql test" not in (info.instructions or "")
    assert prompts[0].startswith("Question: How many issues are open?")
    assert "If empty:" not in prompts[0]


def test_each_run_is_a_fresh_conversation() -> None:
    lengths: list[int] = []
    steps = iter(["SELECT 1", SET_ANSWER, "SELECT 1", SET_ANSWER])

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        lengths.append(len(messages))
        return _scripted(next(steps))(messages, info)

    agent = _agent(script, sql=_executor(SIXTY, SIXTY))
    first, second = agent.run(QUESTION, "sql"), agent.run(QUESTION, "sql")
    assert lengths[0] == lengths[2] == 1
    # The second run's answer names r1 again: handles restart with each run.
    assert [(a.text, a.outcome, a.rows) for a in first.attempts] == [
        (a.text, a.outcome, a.rows) for a in second.attempts
    ]
    assert second.outcome == "answered"
    assert first.value == second.value


def test_ask_runs_the_plain_block_and_takes_the_shape_the_model_states() -> None:
    prompts: list[str] = []
    steps = iter(["SELECT 1", {**SET_ANSWER, "shape": "set"}])

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.extend(str(part.content) for part in first.parts if part.part_kind == "user-prompt")
        return _scripted(next(steps))(messages, info)

    run = _agent(script).ask("Which issues exist?", "sql")
    assert prompts[0] == "Question: Which issues exist?"
    assert run.outcome == "answered"


# --- Ends without an answer (AC-17, AC-21) ---


def _no_answer_fields(run: Any) -> None:
    assert run.shape is None
    assert run.value is None
    assert run.sources == ()


def test_plain_text_is_no_answer_and_keeps_its_attempts_and_usage() -> None:
    run = _agent(_scripted("SELECT 1", "text:I cannot tell")).run(QUESTION, "sql")
    assert run.outcome == "no_answer"
    _no_answer_fields(run)
    assert len(run.attempts) == 1
    assert run.usage.requests == 2


def test_a_wrong_answer_is_sent_back_and_a_corrected_one_passes() -> None:
    wrong = {**SET_ANSWER, "answer_result": "r9"}
    retries: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        # Pydantic AI 2.61 returns an output tool's check failure as its tool return.
        kinds = ("retry-prompt", "tool-return")
        retries.extend(str(part.content) for part in last.parts if part.part_kind in kinds)
        return steps(messages, info)

    steps = _scripted("SELECT 1", wrong, SET_ANSWER)
    run = _agent(script).run(QUESTION, "sql")
    assert run.outcome == "answered"
    assert any("there is no result r9" in text for text in retries)


def test_three_failed_answers_end_as_no_answer() -> None:
    wrong = {**SET_ANSWER, "shape": "number"}
    run = _agent(_scripted("SELECT 1", wrong, wrong, wrong)).run(QUESTION, "sql")
    assert run.outcome == "no_answer"
    _no_answer_fields(run)
    assert run.usage.requests == 4


def test_a_store_error_is_text_for_the_model_and_uses_a_tool_call_not_a_retry() -> None:
    from orgos.sqlstore.query import SqlQueryError

    texts: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        texts.extend(str(p.content) for p in last.parts if p.part_kind == "tool-return")
        assert not any(p.part_kind == "retry-prompt" for p in last.parts)
        return steps(messages, info)

    answer = {
        **SET_ANSWER,
        "answer_result": "r2",
        "sources": [{"issue": {"result": "r2", "column": "id"}}],
    }
    steps = _scripted("SELECT x", "SELECT 1", answer)
    sql = _executor(SqlQueryError('column "x" does not exist'), SIXTY)
    run = _agent(script, sql=sql).run(QUESTION, "sql")
    assert texts[0] == 'error, no handle: column "x" does not exist'
    assert run.outcome == "answered"
    assert [a.outcome for a in run.attempts] == ["error", "ok"]


@pytest.mark.parametrize(
    ("limits", "steps"),
    [
        (
            LIMITS.__class__(**{**LIMITS.__dict__, "request_limit": 2}),
            ("SELECT 1", "SELECT 2", "SELECT 3"),
        ),
        (
            LIMITS.__class__(**{**LIMITS.__dict__, "tool_call_limit": 1}),
            ("SELECT 1", "SELECT 2", "SELECT 3"),
        ),
        (
            LIMITS.__class__(**{**LIMITS.__dict__, "token_limit": 60}),
            ("SELECT 1", "SELECT 2", "SELECT 3"),
        ),
    ],
    ids=["requests", "tool_calls", "tokens"],
)
def test_each_limit_ends_as_limit_hit_with_its_usage_kept(
    limits: Limits, steps: tuple[str, ...]
) -> None:
    run = _agent(_scripted(*steps), sql=_executor(SIXTY, SIXTY, SIXTY), limits=limits).run(
        QUESTION, "sql"
    )
    assert run.outcome == "limit_hit"
    _no_answer_fields(run)
    assert run.usage.requests >= 1
    assert run.usage.input_tokens > 0
    assert run.usage.tool_calls == len(run.attempts)


def test_fifteen_queries_then_an_answer_is_within_the_tool_call_limit() -> None:
    steps = [f"SELECT {n}" for n in range(15)]
    answer = {
        **SET_ANSWER,
        "answer_result": "r15",
        "sources": [{"issue": {"result": "r15", "column": "id"}}],
    }
    sql = _executor(*([SIXTY] * 15))
    run = _agent(_scripted(*steps, answer), sql=sql).run(QUESTION, "sql")
    assert run.outcome == "answered"
    assert run.usage.tool_calls == 15


def test_a_timed_out_last_attempt_then_text_is_store_timeout() -> None:
    sql = _executor(SqlQueryTimeout("the SQL query ran past the 30 s timeout"))
    run = _agent(_scripted("SELECT pg_sleep(60)", "text:it timed out"), sql=sql).run(
        QUESTION, "sql"
    )
    assert run.outcome == "store_timeout"
    _no_answer_fields(run)


def test_a_timeout_then_an_answer_is_answered() -> None:
    sql = _executor(SqlQueryTimeout("timeout"), SIXTY)
    answer = {
        **SET_ANSWER,
        "answer_result": "r2",
        "sources": [{"issue": {"result": "r2", "column": "id"}}],
    }
    run = _agent(_scripted("SELECT slow", "SELECT 1", answer), sql=sql).run(QUESTION, "sql")
    assert run.outcome == "answered"
    assert [a.outcome for a in run.attempts] == ["store_timeout", "ok"]


def test_a_failure_of_our_own_code_inside_a_run_is_error() -> None:
    run = _agent(_scripted("SELECT 1"), sql=_executor(RuntimeError("bug"))).run(QUESTION, "sql")
    assert run.outcome == "error"
    _no_answer_fields(run)


# --- Outages and refusals stop the command (AC-21, AC-22) ---


@pytest.mark.parametrize(
    "error",
    [
        ModelHTTPError(status_code=500, model_name="m", body={"error": "server"}),
        ModelHTTPError(status_code=529, model_name="m"),
        ModelHTTPError(status_code=429, model_name="m"),
        ModelConnectionError(model_name="m", message="connection refused"),
    ],
)
def test_a_provider_outage_raises(error: Exception) -> None:
    with pytest.raises(ProviderOutage):
        _agent(_scripted(error)).run(QUESTION, "sql")


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_provider_refusal_raises_agent_error(status: int) -> None:
    with pytest.raises(AgentError, match=f"HTTP {status}"):
        _agent(_scripted(ModelHTTPError(status_code=status, model_name="m"))).run(QUESTION, "sql")


def test_a_lost_store_connection_raises_store_outage() -> None:
    sql = _executor(SqlConnectionLost("lost the connection to Postgres"))
    with pytest.raises(StoreOutage):
        _agent(_scripted("SELECT 1"), sql=sql).run(QUESTION, "sql")


# covers: review 2026-11-11 Minor 1, a run that raises keeps its usage
def test_a_store_outage_after_a_model_request_carries_its_cost() -> None:
    sql = _executor(SqlConnectionLost("lost the connection to Postgres"))
    with pytest.raises(StoreOutage) as raised:
        _agent(_scripted("SELECT 1"), sql=sql).run(QUESTION, "sql")
    assert raised.value.cost_usd > 0
    assert str(raised.value) == "lost the connection to Postgres"


# covers: review 2026-11-11 Minor 1, a run that raises keeps its usage
@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (ModelHTTPError(status_code=529, model_name="m"), ProviderOutage),
        (ModelHTTPError(status_code=400, model_name="m"), ProviderRefusal),
    ],
)
def test_a_provider_error_after_a_model_request_carries_its_cost(
    error: Exception, raised: type[Exception]
) -> None:
    with pytest.raises(raised) as caught:
        _agent(_scripted("SELECT 1", error)).run(QUESTION, "sql")
    assert caught.value.cost_usd > 0  # type: ignore[attr-defined]


def test_a_provider_error_on_the_first_request_costs_nothing() -> None:
    with pytest.raises(ProviderOutage) as caught:
        _agent(_scripted(ModelHTTPError(status_code=529, model_name="m"))).run(QUESTION, "sql")
    assert caught.value.cost_usd == 0


# covers: review 2026-11-11 Minor 2, own_error names its type
def test_own_error_logs_the_type_name_and_not_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="orgos.agent.agent")
    sql = _executor(RuntimeError("a store value"))
    run = _agent(_scripted("SELECT 1"), sql=sql).run(QUESTION, "sql")
    assert run.outcome == "error"
    records = [r for r in caplog.records if r.name == "orgos.agent.agent"]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.WARNING, "a run ended as own_error: RuntimeError")
    ]
    assert "a store value" not in caplog.text


# --- The layout line and the soft layout check (spec 0015 AC-2, AC-3, AC-8 to AC-10) ---

WRONG_LAYOUT = {**SET_ANSWER, "sources": [{"person": {"result": "r1", "column": "id"}}]}
LAYOUT_REASON = "step 1 holds person; the layout asks for issue"


def _feedback(texts: list[str], script: Script) -> Script:
    """`script`, recording every check message sent back to the model in `texts`."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        kinds = ("retry-prompt", "tool-return")
        texts.extend(str(p.content) for p in messages[-1].parts if p.part_kind in kinds)
        return script(messages, info)

    return respond


def _layout_sends(texts: list[str]) -> list[str]:
    return [text for text in texts if "does not follow the Sources layout" in text]


def test_the_right_layout_ends_on_the_first_try(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="orgos.agent.agent")
    texts: list[str] = []
    run = _agent(_feedback(texts, _scripted("SELECT 1", SET_ANSWER))).run(QUESTION, "sql")
    assert run.outcome == "answered"
    assert run.usage.requests == 2
    assert _layout_sends(texts) == []
    assert "layout send back" not in caplog.text


def test_a_wrong_layout_three_times_is_accepted_after_two_send_backs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="orgos.agent.agent")
    texts: list[str] = []
    script = _feedback(texts, _scripted("SELECT 1", WRONG_LAYOUT, WRONG_LAYOUT, WRONG_LAYOUT))
    run = _agent(script).run(QUESTION, "graph")
    assert run.outcome == "answered"
    assert len(run.value) == 60
    assert run.sources == ({"person": tuple(f"CLOUD-{n}" for n in range(60))},)
    assert run.usage.requests == 4
    sends = _layout_sends(texts)
    assert len(sends) == 2
    assert all(LAYOUT_REASON in text for text in sends)
    records = [r.getMessage() for r in caplog.records if r.name == "orgos.agent.agent"]
    assert records == [
        f"layout send back: Q01 graph retry 0: {LAYOUT_REASON}",
        f"layout send back: Q01 graph retry 1: {LAYOUT_REASON}",
    ]


def test_a_wrong_layout_then_a_right_one_is_accepted_after_one_send_back() -> None:
    texts: list[str] = []
    run = _agent(_feedback(texts, _scripted("SELECT 1", WRONG_LAYOUT, SET_ANSWER))).run(
        QUESTION, "sql"
    )
    assert run.outcome == "answered"
    assert run.sources == ({"issue": tuple(f"CLOUD-{n}" for n in range(60))},)
    assert len(_layout_sends(texts)) == 1


def test_a_structural_problem_is_sent_alone_and_the_layout_only_after_it_is_fixed() -> None:
    texts: list[str] = []
    structural = {**WRONG_LAYOUT, "answer_result": "r9"}
    script = _feedback(texts, _scripted("SELECT 1", structural, WRONG_LAYOUT, SET_ANSWER))
    run = _agent(script).run(QUESTION, "sql")
    assert run.outcome == "answered"
    sent = [t for t in texts if "not accepted yet" in t or "Sources layout" in t]
    assert len(sent) == 2
    assert "there is no result r9" in sent[0]
    assert "Sources layout" not in sent[0]
    assert LAYOUT_REASON in sent[1]
    assert "not accepted yet" not in sent[1]


def test_a_structural_problem_after_two_layout_send_backs_ends_as_no_answer() -> None:
    structural = {**SET_ANSWER, "answer_result": "r9"}
    script = _scripted("SELECT 1", WRONG_LAYOUT, WRONG_LAYOUT, structural)
    run = _agent(script).run(QUESTION, "sql")
    assert run.outcome == "no_answer"
    _no_answer_fields(run)


def test_a_zero_answer_with_any_sources_is_not_checked() -> None:
    empty = QueryResult(("id",), ())
    texts: list[str] = []
    run = _agent(_feedback(texts, _scripted("SELECT 1", WRONG_LAYOUT)), sql=_executor(empty)).run(
        QUESTION, "sql"
    )
    assert run.outcome == "answered"
    assert run.value == []
    assert _layout_sends(texts) == []


def test_ask_has_no_layout_line_and_no_layout_check(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="orgos.agent.agent")
    prompts: list[str] = []
    texts: list[str] = []
    inner = _scripted("SELECT 1", WRONG_LAYOUT)

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.extend(str(part.content) for part in first.parts if part.part_kind == "user-prompt")
        return inner(messages, info)

    run = _agent(_feedback(texts, script)).ask("Which issues exist?", "sql")
    assert prompts[0] == "Question: Which issues exist?"
    assert run.outcome == "answered"
    assert run.usage.requests == 2
    assert _layout_sends(texts) == []
    assert "layout send back" not in caplog.text


def test_a_run_on_a_question_with_no_layout_is_an_agent_error() -> None:
    other = QUESTION.model_copy(update={"id": "Q02"})
    with pytest.raises(AgentError, match=r"layouts\.toml has no layout for Q02"):
        _agent(_scripted()).run(other, "sql")


def test_both_stores_see_the_same_block_ending_in_its_layout_line_for_all_20() -> None:
    from orgos.agent.agent import read_layouts, read_questions

    questions = read_questions()
    layouts = read_layouts(questions)
    assert len(questions) == 20
    prompts: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.extend(str(part.content) for part in first.parts if part.part_kind == "user-prompt")
        return ModelResponse(parts=[TextPart("cannot say")])

    agent = _agent(script, layouts=dict(layouts))
    for question in questions:
        prompts.clear()
        agent.run(question, "sql")
        agent.run(question, "graph")
        sql, graph = prompts
        assert sql == graph, question.id
        before, last = sql.rsplit("\n", 1)
        assert last == render_layout_line(layouts[question.id], question.shape), question.id
        assert before.endswith("Reading note:\n" + visible_reading(question.reading)), question.id


# --- Usage and cost (AC-23) ---


def test_the_cost_uses_the_four_prices() -> None:
    from pydantic_ai.usage import RunUsage

    from orgos.agent.models import cost_usd

    usage = RunUsage(
        input_tokens=21_100, cache_write_tokens=1_000, cache_read_tokens=20_000, output_tokens=7
    )
    expected = (100 * 2.0 + 1_000 * 2.5 + 20_000 * 0.2 + 7 * 10.0) / 1_000_000
    assert cost_usd(usage, PRICES) == pytest.approx(expected)


def test_a_run_reports_its_cost() -> None:
    run = _agent(_scripted("SELECT 1", SET_ANSWER)).run(QUESTION, "sql")
    expected = (run.usage.input_tokens * 2.0 + run.usage.output_tokens * 10.0) / 1_000_000
    assert run.usage.cost_usd == pytest.approx(expected)


# --- The version (AC-24) ---


def _parts(**changes: Any) -> dict[str, str]:
    base: dict[str, Any] = {
        "model_id": "test:scripted",
        "limits": LIMITS,
        "instructions": {"sql": ("a", "b"), "graph": ("c", "d")},
        "descriptions": DESCRIPTIONS,
        "question_blocks": ("Question: one", "Question: two"),
        "meanings_text": "meanings",
    }
    return version_parts(**(base | changes))


def test_the_same_inputs_give_the_same_hash() -> None:
    assert version_hash(_parts()) == version_hash(_parts())
    agent = _agent(_scripted())
    assert agent.describe().version_hash == _agent(_scripted()).describe().version_hash
    description = agent.describe()
    assert description.price_table == "prices.toml@2026-01-01"
    assert (description.usd_per_million_input, description.usd_per_million_output) == (2.0, 10.0)
    assert set(description.parts) == {
        "instructions",
        "question_blocks",
        "tools",
        "description_sql",
        "description_graph",
        "meanings",
        "model",
        "settings",
    }


@pytest.mark.parametrize(
    ("change", "part"),
    [
        ({"model_id": "test:other"}, "model"),
        ({"limits": LIMITS.__class__(**{**LIMITS.__dict__, "max_rows": 49})}, "settings"),
        ({"instructions": {"sql": ("a", "b2"), "graph": ("c", "d")}}, "instructions"),
        ({"question_blocks": ("Question: two", "Question: one")}, "question_blocks"),
        ({"meanings_text": "meanings 2"}, "meanings"),
        (
            {"descriptions": DESCRIPTIONS | {"sql": Description(text="x", keys=frozenset())}},
            "description_sql",
        ),
        (
            {"descriptions": DESCRIPTIONS | {"graph": Description(text="x", keys=frozenset())}},
            "description_graph",
        ),
    ],
)
def test_a_change_in_any_part_changes_the_hash(change: dict[str, Any], part: str) -> None:
    before, after = _parts(), _parts(**change)
    assert before[part] != after[part]
    assert {name for name in before if before[name] != after[name]} == {part}
    assert version_hash(before) != version_hash(after)


# covers: AC-20, AC-24, sampling is recorded as provider default; a set temperature changes it
def test_a_temperature_changes_only_the_settings_part() -> None:
    warmer = LIMITS.__class__(**{**LIMITS.__dict__, "temperature": 0.3})
    before, after = _parts(), _parts(limits=warmer)
    assert {name for name in before if before[name] != after[name]} == {"settings"}
    assert version_hash(before) != version_hash(after)


def test_a_library_version_change_changes_the_settings_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pydantic_ai

    before = _parts()
    monkeypatch.setattr(pydantic_ai, "__version__", "0.0.0")
    assert _parts()["settings"] != before["settings"]
