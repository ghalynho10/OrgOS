"""The provider module: one request, the error mapping, the model (spec 0012 AC-20, AC-22, AC-23).

The request test runs the real Anthropic client and model class against a fake
HTTP transport, so the exact body is seen and nothing leaves the machine. It
checks the build Follow-up: no forced tool choice while plain text may end a
run, adaptive thinking at effort low, the five minute cache, and the model name
read from the response. Whether the provider accepts those settings is for the
paid trial.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelHTTPError,
    ModelRateLimitError,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from orgos.agent.agent import OrgosQueryAgent
from orgos.agent.errors import AgentError
from orgos.agent.models import CLIENT_RETRIES, build_model, model_settings, provider_error
from orgos.config import ConfigError
from orgos.eval.handoff import ProviderOutage
from tests.test_agent_run import (
    DESCRIPTIONS,
    LAYOUTS,
    LIMITS,
    PRICES,
    QUESTION,
    _executor,
    _offline_settings,
)

MESSAGE = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5-20261001",
    "content": [{"type": "text", "text": "I cannot tell"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 5,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 0,
    },
}


def _sent_request() -> tuple[dict[str, Any], Any]:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=MESSAGE)

    client = AsyncAnthropic(
        api_key=secrets.token_hex(8),
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    model = AnthropicModel("claude-sonnet-5-5", provider=AnthropicProvider(anthropic_client=client))
    agent = OrgosQueryAgent(
        model=model,
        model_id="anthropic:claude-sonnet-5-5",
        prices=PRICES,
        limits=LIMITS,
        executors={"sql": _executor(), "graph": _executor()},
        descriptions=DESCRIPTIONS,
        question_blocks=("Question: one",),
        layouts=LAYOUTS,
        meanings_text="meanings",
    )
    run = agent.run(QUESTION, "sql")
    return bodies[0], run


def test_the_request_forces_no_tool_and_carries_the_fixed_settings() -> None:
    body, _ = _sent_request()
    assert body["model"] == "claude-sonnet-5-5"
    assert body.get("tool_choice", {"type": "auto"}) == {"type": "auto"}
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert body["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    assert body["max_tokens"] == LIMITS.max_tokens
    for sampling in ("temperature", "top_p", "top_k"):
        assert sampling not in body
    assert [tool["name"] for tool in body["tools"]] == ["run_query", "final_answer"]


def test_the_returned_model_name_and_the_cache_tokens_reach_the_run() -> None:
    _, run = _sent_request()
    assert run.response_model == "claude-sonnet-5-5-20261001"
    assert run.outcome == "no_answer"
    # Pydantic AI's input_tokens holds the cache write tokens: 100 uncached + 1,000 written.
    assert run.usage.input_tokens == 1100
    assert run.usage.requests == 1
    expected = (100 * PRICES.input + 1000 * PRICES.cache_write + 5 * PRICES.output) / 1_000_000
    assert run.usage.cost_usd == pytest.approx(expected)


# --- The error mapping (AC-21, AC-22) ---


@pytest.mark.parametrize(
    "error",
    [
        ModelHTTPError(status_code=500, model_name="m"),
        ModelHTTPError(status_code=503, model_name="m"),
        ModelHTTPError(status_code=429, model_name="m"),
        ModelHTTPError.for_category(ModelRateLimitError, status_code=429, model_name="m"),
        ModelConnectionError(model_name="m", message="connection reset"),
    ],
)
def test_connection_errors_5xx_and_429_are_outages(error: ModelAPIError) -> None:
    assert isinstance(provider_error(error), ProviderOutage)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
def test_other_statuses_are_agent_errors(status: int) -> None:
    mapped = provider_error(ModelHTTPError(status_code=status, model_name="m"))
    assert isinstance(mapped, AgentError)
    assert f"HTTP {status}" in str(mapped)


# --- The model (AC-20, AC-22) ---


# covers: AC-20, no temperature is sent while ORGOS_AGENT_TEMPERATURE is unset
def test_a_temperature_is_sent_only_when_one_is_set() -> None:
    assert model_settings(16_000, None) == {"max_tokens": 16_000}
    assert model_settings(16_000, 0.3) == {"max_tokens": 16_000, "temperature": 0.3}


def test_the_model_has_the_client_retries_and_refuses_another_provider() -> None:
    key = secrets.token_hex(8)
    settings = _offline_settings(model="anthropic:claude-sonnet-5-5", anthropic_api_key=key)
    model = build_model(settings)
    assert isinstance(model, AnthropicModel)
    assert model.model_name == "claude-sonnet-5-5"
    assert model.client.max_retries == CLIENT_RETRIES
    with pytest.raises(ConfigError, match="ORGOS_MODEL must be"):
        build_model(_offline_settings(model="openai:gpt-test", anthropic_api_key=key))
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_model(_offline_settings(model="anthropic:claude-sonnet-5-5", anthropic_api_key=None))
