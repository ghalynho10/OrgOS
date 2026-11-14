"""The query agent: one agent, the same instructions, tools and limits for both stores (spec 0012).

`build_agent(settings)` is what the runner loads as `orgos.agent:build_agent`
(spec 0011 AC-38). It checks both store sessions before any other read, reads
each store's structure and value lists, renders both descriptions and returns
a `QueryAgent`. `OrgosQueryAgent` itself is built from inputs already read,
so tests give it a `FunctionModel` and fake executors and nothing reaches a
model or a store. Each run is a fresh conversation with its own `RunTrace`
(AC-6); the run ends through `final_answer` or plain text (AC-16), its outcome
comes from the pure map in `outcome`, and its value and sources from
`assemble`, never from what the model typed. `run_plain` is the same run on
the plain block of spec 0013, with no layout, and `plain_blocks_sha256` pins
that block, which is outside the version hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pydantic_ai
from pydantic_ai import (
    Agent,
    ModelRetry,
    RunContext,
    Tool,
    ToolOutput,
    capture_run_messages,
)
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.usage import RunUsage, UsageLimits

from orgos.agent import answer as answer_module
from orgos.agent import outcome as outcome_module
from orgos.agent import tools as tools_module
from orgos.agent.answer import (
    ANSWER_DESCRIPTION,
    ANSWER_TOOL,
    FinalAnswer,
    Layout,
    answer_problems,
    assemble,
    layout_send_back,
)
from orgos.agent.describe_graph import read_graph_values, render_graph_description
from orgos.agent.describe_sql import (
    read_sql_structure,
    read_sql_values,
    render_sql_description,
)
from orgos.agent.errors import AgentError
from orgos.agent.meanings import MEANINGS_FILE, Description, load_meanings
from orgos.agent.models import (
    Prices,
    build_model,
    capabilities,
    cost_usd,
    model_settings,
    prices_for,
    provider_error,
    read_prices_file,
    recorded_settings,
)
from orgos.agent.outcome import RunEnd, run_outcome
from orgos.agent.prompt import (
    LAYOUTS_FILE,
    parse_layouts,
    render_instructions,
    render_plain_block,
    render_plain_run_block,
    render_question_block,
)
from orgos.agent.session import check_sessions, read_graph_session, read_sql_session
from orgos.agent.tools import TOOL_DESCRIPTION, Executor, RunTrace, run_query
from orgos.config import STORE_ROW_CAP, ConfigError, Settings
from orgos.eval.checker import effective_tables
from orgos.eval.frozen import AMENDMENTS, QUESTIONS
from orgos.eval.handoff import (
    STORES,
    AgentDescription,
    AgentRun,
    RunStopped,
    Store,
    Usage,
)
from orgos.eval.models import Question, Shape, read_toml
from orgos.graphstore.mapping import load_mapping
from orgos.graphstore.query import reader_driver, run_cypher
from orgos.sqlstore.query import reader_connection, run_sql

AGENT_NAME = "orgos_query_agent"
OUTPUT_RETRIES = 2
TOOL_RETRIES = 2
SAMPLING = "provider default"
ROOT = Path(__file__).resolve().parents[3]
# The sources whose text is part of the tool definitions in the version hash (AC-24).
TOOL_SOURCES = (tools_module, answer_module, outcome_module)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunDeps:
    """What a run's tools and checks see: its trace, and what a scored question asks for.

    `question_id` and `layout` are None in plain question mode, where no
    layout is checked (spec 0015 AC-10).
    """

    trace: RunTrace
    expected_shape: Shape | None
    question_id: str | None = None
    layout: Layout | None = None


@dataclass(frozen=True)
class Limits:
    """The fixed settings of every run (AC-20)."""

    request_limit: int
    tool_call_limit: int
    token_limit: int
    max_tokens: int
    max_rows: int
    temperature: float | None
    query_timeout_s: int

    @classmethod
    def from_settings(cls, settings: Settings) -> Limits:
        """The limits as `Settings` hold them."""
        return cls(
            request_limit=settings.agent_request_limit,
            tool_call_limit=settings.agent_tool_call_limit,
            token_limit=settings.agent_token_limit,
            max_tokens=settings.agent_max_tokens,
            max_rows=settings.agent_max_rows,
            temperature=settings.agent_temperature,
            query_timeout_s=settings.query_timeout_s,
        )


def _query_tool(ctx: RunContext[RunDeps], statement: str) -> str:
    return run_query(ctx.deps.trace, statement)


QUERY_TOOL = Tool(
    _query_tool,
    takes_ctx=True,
    name="run_query",
    description=TOOL_DESCRIPTION,
    sequential=True,
)


def _check_answer(ctx: RunContext[RunDeps], output: FinalAnswer | str) -> FinalAnswer | str:
    if isinstance(output, str):
        return output
    deps = ctx.deps
    problems = answer_problems(output, deps.trace.results, deps.expected_shape)
    if problems:
        raise ModelRetry("final_answer is not accepted yet:\n- " + "\n- ".join(problems))
    # Spec 0015 AC-9: the layout is checked only once the answer itself passes.
    reasons = layout_send_back(output, deps.trace.results, deps.layout, ctx.retry, OUTPUT_RETRIES)
    if reasons:
        log.warning(
            "layout send back: %s %s retry %d: %s",
            deps.question_id,
            deps.trace.store,
            ctx.retry,
            "; ".join(reasons),
        )
        raise ModelRetry(
            "final_answer does not follow the Sources layout:\n- " + "\n- ".join(reasons)
        )
    return output


async def _count_response(
    ctx: RunContext[RunDeps], *, request_context: ModelRequestContext, response: ModelResponse
) -> ModelResponse:
    ctx.deps.trace.requests += 1
    return response


def _new_agent() -> Agent[RunDeps, FinalAnswer | str]:
    # Logfire is not used (spec 0001, Observability): the run records are the trace, so the
    # library's first run banner about observability would only be noise on stderr.
    pydantic_ai.BANNER_ENABLED = False
    counter: Hooks[RunDeps] = Hooks()
    counter.on.after_model_request(_count_response)
    agent: Agent[RunDeps, FinalAnswer | str] = Agent(
        name=AGENT_NAME,
        deps_type=RunDeps,
        output_type=[
            ToolOutput(FinalAnswer, name=ANSWER_TOOL, description=ANSWER_DESCRIPTION),
            str,
        ],
        tools=[QUERY_TOOL],
        retries={"tools": TOOL_RETRIES, "output": OUTPUT_RETRIES},
        capabilities=[*capabilities(), counter],
    )
    agent.output_validator(_check_answer)
    return agent


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def blocks_sha256(blocks: Sequence[str]) -> str:
    """The sha256 of rendered question blocks in order: the `question_blocks` part (AC-24)."""
    return _sha256(json.dumps(list(blocks)))


def tool_definitions() -> list[dict[str, Any]]:
    """The tool and answer definitions as sent to the model, in a stable form."""
    query = QUERY_TOOL.tool_def
    return [
        {
            "name": query.name,
            "description": query.description,
            "parameters": query.parameters_json_schema,
        },
        {
            "name": ANSWER_TOOL,
            "description": ANSWER_DESCRIPTION,
            "parameters": FinalAnswer.model_json_schema(),
        },
        {"plain_text_output": True},
    ]


def version_parts(
    *,
    model_id: str,
    limits: Limits,
    instructions: Mapping[Store, tuple[str, ...]],
    descriptions: Mapping[Store, Description],
    question_blocks: tuple[str, ...],
    meanings_text: str,
) -> dict[str, str]:
    """The sha256 of each part the model sees or that shapes a run (AC-24)."""
    tools_text = json.dumps(tool_definitions(), sort_keys=True) + "".join(
        Path(module.__file__ or "").read_text(encoding="utf-8") for module in TOOL_SOURCES
    )
    recorded = {
        "request_limit": limits.request_limit,
        "tool_call_limit": limits.tool_call_limit,
        "token_limit": limits.token_limit,
        "max_tokens": limits.max_tokens,
        "max_rows": limits.max_rows,
        "row_cap": STORE_ROW_CAP,
        "query_timeout_s": limits.query_timeout_s,
        "sampling": SAMPLING if limits.temperature is None else f"temperature {limits.temperature}",
        "output_retries": OUTPUT_RETRIES,
        "tool_retries": TOOL_RETRIES,
        "pydantic_ai": pydantic_ai.__version__,
        **recorded_settings(),
    }
    return {
        "instructions": _sha256(json.dumps({s: list(instructions[s]) for s in STORES})),
        "question_blocks": blocks_sha256(question_blocks),
        "tools": _sha256(tools_text),
        "description_sql": _sha256(descriptions["sql"].text),
        "description_graph": _sha256(descriptions["graph"].text),
        "meanings": _sha256(meanings_text),
        "model": _sha256(model_id),
        "settings": _sha256(json.dumps(recorded, sort_keys=True)),
    }


def version_hash(parts: Mapping[str, str]) -> str:
    """The sha256 of the sorted `name:hash` lines of the parts (AC-24)."""
    return _sha256("\n".join(sorted(f"{name}:{digest}" for name, digest in parts.items())))


def _response_model(messages: list[ModelMessage]) -> str | None:
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    return responses[-1].model_name if responses else None


class OrgosQueryAgent:
    """The agent the runner and `orgos ask` use: `describe`, `run`, `run_plain` and `ask`.

    `run` and `ask` are plain methods (AC-1); the open store connections live
    as long as the object, for the one command that built it.
    """

    def __init__(
        self,
        *,
        model: Model,
        model_id: str,
        prices: Prices,
        limits: Limits,
        executors: Mapping[Store, Executor],
        descriptions: Mapping[Store, Description],
        question_blocks: tuple[str, ...],
        layouts: Mapping[str, Layout],
        meanings_text: str,
        resources: ExitStack | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._model_id = model_id
        self._prices = prices
        self._limits = limits
        self._executors = dict(executors)
        self._layouts = dict(layouts)
        self._instructions = {
            store: (render_instructions(store, limits.max_rows), descriptions[store].text)
            for store in STORES
        }
        self._agent = _new_agent()
        self._resources = resources
        self._clock = clock
        parts = version_parts(
            model_id=model_id,
            limits=limits,
            instructions=self._instructions,
            descriptions=descriptions,
            question_blocks=question_blocks,
            meanings_text=meanings_text,
        )
        self._description = AgentDescription(
            model_id=model_id,
            version_hash=version_hash(parts),
            parts=parts,
            price_table=prices.table,
            usd_per_million_input=prices.input,
            usd_per_million_output=prices.output,
        )

    def describe(self) -> AgentDescription:
        """The model, version hash, part hashes and prices of this agent (AC-24)."""
        return self._description

    def run(self, question: Question, store: Store) -> AgentRun:
        """Answer one question against one store, scored by the runner.

        The one layout entry of the question gives both the block's line and
        the check, so the two cannot drift (spec 0015 AC-2).
        """
        layout = self._layouts.get(question.id)
        if layout is None:
            raise AgentError(f"{LAYOUTS_FILE.name} has no layout for {question.id}")
        block = render_question_block(question, layout)
        return self._run(block, store, question.shape, question.id, layout)

    def run_plain(self, question: Question, store: Store) -> AgentRun:
        """Answer one question from its plain block, scored by the runner (spec 0013 AC-4).

        The same agent as `run`, and the answer check enforces the question's
        shape, but with no question id and no layout, so no layout check runs.
        """
        return self._run(render_plain_run_block(question), store, question.shape, None, None)

    def plain_blocks_sha256(self, questions: Sequence[Question]) -> str:
        """The hash of the plain blocks of `questions` in id order (spec 0013 AC-7)."""
        return blocks_sha256(read_plain_blocks(questions))

    def ask(self, text: str, store: Store) -> AgentRun:
        """Answer a plain question: the same agent with the question text only (AC-5)."""
        return self._run(render_plain_block(text), store, None, None, None)

    def _run(
        self,
        block: str,
        store: Store,
        expected_shape: Shape | None,
        question_id: str | None,
        layout: Layout | None,
    ) -> AgentRun:
        limits = self._limits
        trace = RunTrace(store=store, execute=self._executors[store], max_rows=limits.max_rows)
        usage = RunUsage()
        answer: FinalAnswer | None = None
        value: Any = None
        sources: tuple[Any, ...] = ()
        started = self._clock()
        with capture_run_messages() as messages:
            try:
                result = self._agent.run_sync(
                    block,
                    model=self._model,
                    deps=RunDeps(
                        trace=trace,
                        expected_shape=expected_shape,
                        question_id=question_id,
                        layout=layout,
                    ),
                    instructions=list(self._instructions[store]),
                    model_settings=model_settings(limits.max_tokens, limits.temperature),
                    usage_limits=UsageLimits(
                        request_limit=limits.request_limit,
                        tool_calls_limit=limits.tool_call_limit,
                        total_tokens_limit=limits.token_limit,
                    ),
                    usage=usage,
                )
                if isinstance(result.output, FinalAnswer):
                    answer = result.output
                    value, sources = assemble(answer, trace.results)
                end: RunEnd = "final_answer" if answer is not None else "plain_text"
            except UsageLimitExceeded:
                end = "usage_limit"
            except UnexpectedModelBehavior:
                end = "model_failure"
            # AC-23: a run that stops the command keeps its usage on the error it raises.
            except RunStopped as error:
                raise error.restated(cost_usd=cost_usd(usage, self._prices)) from None
            except AgentError:
                raise
            except ModelAPIError as error:
                stop = provider_error(error)
                raise stop.restated(cost_usd=cost_usd(usage, self._prices)) from None
            # AC-21: a failure of our own code ends this run as `error`, not the command.
            # Only the type is logged: the message may hold store values.
            except Exception as error:  # noqa: BLE001
                log.warning("a run ended as own_error: %s", type(error).__name__)
                end = "own_error"
        seconds = self._clock() - started
        outcome = run_outcome(end, trace.attempts)
        answered = outcome == "answered" and answer is not None
        return AgentRun(
            response_model=_response_model(messages),
            outcome=outcome,
            shape=cast(Shape, answer.shape) if answered and answer is not None else None,
            value=value if answered else None,
            sources=sources if answered else (),
            attempts=tuple(trace.attempts),
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                requests=trace.requests,
                tool_calls=len(trace.attempts),
                cost_usd=cost_usd(usage, self._prices),
            ),
            seconds=seconds,
        )


def read_questions(root: Path = ROOT) -> tuple[Question, ...]:
    """The 20 questions in id order, amendments applied."""
    questions = read_toml(root / QUESTIONS).get("question", [])
    amended = (
        read_toml(root / AMENDMENTS).get("amendment", []) if (root / AMENDMENTS).is_file() else []
    )
    return tuple(
        sorted(
            (Question.model_validate(table) for table in effective_tables(questions, amended)),
            key=lambda question: question.id,
        )
    )


def read_layouts(
    questions: tuple[Question, ...], path: Path = LAYOUTS_FILE
) -> Mapping[str, Layout]:
    """The checked layout of each question, read from `layouts.toml` (spec 0015 AC-1)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise AgentError(f"Cannot read {path}: {error.strerror}") from None
    return parse_layouts(text, {question.id: question.shape for question in questions})


def read_question_blocks(
    questions: tuple[Question, ...], layouts: Mapping[str, Layout]
) -> tuple[str, ...]:
    """The 20 rendered question blocks in id order, each with its layout, for the version hash."""
    return tuple(render_question_block(question, layouts[question.id]) for question in questions)


def read_plain_blocks(questions: Sequence[Question]) -> tuple[str, ...]:
    """The plain block of each question, in id order whatever order they are given in."""
    return tuple(
        render_plain_run_block(question) for question in sorted(questions, key=lambda q: q.id)
    )


def build_agent(settings: Settings) -> OrgosQueryAgent:
    """Check both sessions, read both stores, render both descriptions and build the agent (AC-1).

    Raises `ConfigError` when `ORGOS_MODEL` is unset, and `AgentError` when a
    session check fails or the model has no price entry, before any run.
    """
    if settings.model is None:
        raise ConfigError("ORGOS_MODEL not set; the agent needs a model, see .env.example")
    prices = prices_for(settings.model, read_prices_file())
    questions = read_questions()
    layouts = read_layouts(questions)
    model = build_model(settings)
    limits = Limits.from_settings(settings)
    timeout = float(settings.query_timeout_s)
    mapping, meanings = load_mapping(), load_meanings()
    resources = ExitStack()
    try:
        conn = resources.enter_context(reader_connection(settings))
        driver = resources.enter_context(reader_driver(settings))
        check_sessions(
            read_sql_session(conn, timeout_s=timeout),
            read_graph_session(driver),
            reader_login=settings.pg_reader_user,
        )
        descriptions: dict[Store, Description] = {
            "sql": render_sql_description(
                read_sql_structure(conn, timeout_s=timeout),
                meanings,
                read_sql_values(conn, timeout_s=timeout),
            ),
            "graph": render_graph_description(
                mapping, meanings, read_graph_values(driver, mapping, timeout_s=timeout)
            ),
        }
    except BaseException:
        resources.close()
        raise
    executors: dict[Store, Executor] = {
        "sql": lambda statement: run_sql(conn, statement, timeout_s=timeout),
        "graph": lambda statement: run_cypher(driver, statement, timeout_s=timeout),
    }
    return OrgosQueryAgent(
        model=model,
        model_id=settings.model,
        prices=prices,
        limits=limits,
        executors=executors,
        descriptions=descriptions,
        question_blocks=read_question_blocks(questions, layouts),
        layouts=layouts,
        meanings_text=MEANINGS_FILE.read_text(encoding="utf-8"),
        resources=resources,
    )
