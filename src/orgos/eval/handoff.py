"""Everything the eval runner exchanges with the query agent (spec 0011 AC-25 to AC-28).

The runner talks to the agent only through these types, so the agent (feature
13, `orgos.agent`) imports this module and the runner never imports the agent;
it loads one by a dotted path at run time (AC-38). The agent receives the
`Question` model and a store name. No type here holds a known answer.
`QueryAgent.run` is a plain method, not a coroutine (spec 0012 AC-1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self

from orgos.eval import EvalError
from orgos.eval.models import Question, Shape

Store = Literal["sql", "graph"]
STORES: tuple[Store, ...] = ("sql", "graph")

# One store query the agent made, as the agent labels it (AC-25, amended by spec 0012).
AttemptOutcome = Literal["ok", "error", "rejected", "store_timeout", "row_cap"]
# How one agent run ended (AC-10, AC-25).
RunOutcome = Literal["answered", "no_answer", "limit_hit", "store_timeout", "error"]
# What the runner scores a gold pair or an agent run as (AC-23, AC-25).
ScoredOutcome = Literal[
    "correct",
    "wrong",
    "malformed",
    "store_timeout",
    "row_cap",
    "limit_hit",
    "no_answer",
    "error",
    "discrepancy",
    "not_tried",
]
SCORED_OUTCOMES: tuple[ScoredOutcome, ...] = (
    "correct",
    "wrong",
    "malformed",
    "store_timeout",
    "row_cap",
    "limit_hit",
    "no_answer",
    "error",
    "discrepancy",
    "not_tried",
)
# A finished source step: each kind with its IDs, an empty list kept (AC-26).
SourceStep = Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class AgentDescription:
    """Who the agent is: its model, its version hash and the hash of each part it sees (AC-32)."""

    model_id: str
    version_hash: str
    parts: Mapping[str, str]
    price_table: str
    usd_per_million_input: float
    usd_per_million_output: float


@dataclass(frozen=True)
class QueryAttempt:
    """One store query of a run, in call order: its text, outcome, seconds and row count."""

    text: str
    outcome: AttemptOutcome
    seconds: float
    rows: int


@dataclass(frozen=True)
class Usage:
    """What one run used: tokens, requests, tool calls and its cost in US dollars."""

    input_tokens: int
    output_tokens: int
    requests: int
    tool_calls: int
    cost_usd: float


@dataclass(frozen=True)
class AgentRun:
    """One run of the agent on one question and store, as the runner scores it.

    `response_model` is the model name the API returned, or None (spec 0012
    AC-25). `shape`, `value` and `sources` are the raw answer `store_answer`
    takes; a run without an answer has `shape=None`, `value=None` and no sources.
    """

    response_model: str | None
    outcome: RunOutcome
    shape: Shape | None
    value: Any
    sources: tuple[SourceStep, ...]
    attempts: tuple[QueryAttempt, ...]
    usage: Usage
    seconds: float


class QueryAgent(Protocol):
    """The agent as the runner sees it: one description, and one run per question and store."""

    def describe(self) -> AgentDescription:
        """The model, version hash, part hashes and prices of this agent."""
        ...

    def run(self, question: Question, store: Store) -> AgentRun:
        """Answer one question against one store; a plain method, not a coroutine."""
        ...


class RunStopped(Exception):
    """A run that stopped the command instead of ending, with what it cost (spec 0012 AC-23).

    No run file is written for it, so its cost travels on the error: the runner
    adds it to the cost spent so far and prints it, and a rerun fills the run number.
    """

    def __init__(self, message: str, *, cost_usd: float = 0.0) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd

    def restated(self, message: str | None = None, *, cost_usd: float | None = None) -> Self:
        """The same kind of error with a new message or cost; this one is left as it is."""
        return type(self)(
            str(self) if message is None else message,
            cost_usd=self.cost_usd if cost_usd is None else cost_usd,
        )


class ProviderOutage(RunStopped, EvalError):
    """A connection error, an HTTP 5xx or a 429 left after the client's backoff (AC-28)."""


class StoreOutage(RunStopped, EvalError):
    """A lost connection to a store during a run (AC-28, amended by spec 0012)."""
