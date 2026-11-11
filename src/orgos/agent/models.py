"""Everything that depends on the model provider, in one module (spec 0012 AC-2).

Spec 0001 makes this the one ports and adapters exception: the model built from
`ORGOS_MODEL`, prompt caching, thinking, the client retry count and the price
lookup live here and nowhere else in `orgos.agent`, so another provider or a
local model is a change to this module and the configuration only.

The prices come from `prices.toml`, which you fill from the provider's page;
the reader refuses a model with no entry, or an entry with a missing value,
rather than guess (AC-23).
"""

from __future__ import annotations

import datetime as dt
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from pydantic_ai.capabilities import AbstractCapability, Caching, Thinking
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelHTTPError,
    ModelRateLimitError,
    ModelServerError,
    ModelUnavailableError,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import CacheRetention, ModelSettings, ThinkingEffort
from pydantic_ai.usage import RunUsage

from orgos.agent.errors import AgentError
from orgos.config import ConfigError, Settings
from orgos.eval.handoff import ProviderOutage

PRICES_FILE = Path(__file__).with_name("prices.toml")
PRICE_KEYS = ("input", "output", "cache_write", "cache_read")
ENTRY_KEYS = ("page", "date", *PRICE_KEYS)
PROVIDER = "anthropic"
# Fixed for the first version and recorded in the version hash (AC-20, AC-22).
THINKING_EFFORT: ThinkingEffort = "low"
CACHE_RETENTION: CacheRetention = "5m"
CLIENT_RETRIES = 4
PER_MILLION = 1_000_000


def build_model(settings: Settings) -> Model:
    """The model of `ORGOS_MODEL`, with the client's own retries set to `CLIENT_RETRIES`."""
    model_id, api_key = settings.model or "", settings.anthropic_api_key
    provider, _, name = model_id.partition(":")
    if provider != PROVIDER or not name:
        raise ConfigError(f"ORGOS_MODEL must be {PROVIDER}:<model name>, not {model_id!r}")
    if api_key is None:
        raise ConfigError("ANTHROPIC_API_KEY not set; the agent needs it to call the model")
    client = AsyncAnthropic(api_key=api_key.get_secret_value(), max_retries=CLIENT_RETRIES)
    return AnthropicModel(name, provider=AnthropicProvider(anthropic_client=client))


def capabilities() -> tuple[AbstractCapability[Any], ...]:
    """Adaptive thinking at effort low and prompt caching with the five minute cache (AC-20).

    Neither is needed for a correct answer; both can be dropped for a local model.
    """
    return (Thinking(effort=THINKING_EFFORT), Caching(CACHE_RETENTION))


def model_settings(max_tokens: int, temperature: float | None) -> ModelSettings:
    """`max_tokens` per response, and a temperature only when one is set (none is, AC-20)."""
    settings = ModelSettings(max_tokens=max_tokens)
    if temperature is not None:
        settings["temperature"] = temperature
    return settings


def recorded_settings() -> dict[str, Any]:
    """The provider settings of this module, as the version hash records them (AC-24)."""
    return {
        "thinking": f"adaptive, effort {THINKING_EFFORT}",
        "caching": CACHE_RETENTION,
        "client_retries": CLIENT_RETRIES,
    }


def cost_usd(usage: RunUsage, prices: Prices) -> float:
    """The cost of one run from its usage and the model's four prices (AC-23).

    Pydantic AI's `input_tokens` already holds the cache write and cache read
    tokens (checked on its Anthropic usage extraction), so the uncached input
    is what remains after both are taken out.
    """
    uncached = usage.input_tokens - usage.cache_write_tokens - usage.cache_read_tokens
    return (
        uncached * prices.input
        + usage.cache_write_tokens * prices.cache_write
        + usage.cache_read_tokens * prices.cache_read
        + usage.output_tokens * prices.output
    ) / PER_MILLION


def provider_error(error: ModelAPIError) -> ProviderOutage | AgentError:
    """A provider failure as what stops the command: an outage, or a refusal of the request.

    `ProviderOutage` for a connection error, an HTTP 5xx or a 429 left after the
    client's retries (AC-22), and for an error event the provider sends in a
    stream under the same categories (overloaded, server error, rate limit).
    Any other status, a 400, 401, 403 or 404 among them, is an `AgentError`.
    """
    status = error.status_code if isinstance(error, ModelHTTPError) else None
    outage = isinstance(
        error, ModelConnectionError | ModelRateLimitError | ModelServerError | ModelUnavailableError
    ) or (status is not None and (status == 429 or status >= 500))
    shown = f"HTTP {status}" if status is not None else type(error).__name__
    if outage:
        return ProviderOutage(f"the model provider failed ({shown}): {error.message}")
    return AgentError(f"the model provider refused the request ({shown}): {error.message}")


@dataclass(frozen=True)
class Prices:
    """One model's prices in US dollars per million tokens, with where and when they were read."""

    page: str
    date: dt.date
    input: float
    output: float
    cache_write: float
    cache_read: float

    @property
    def table(self) -> str:
        """The price table name a version records: `prices.toml@<date>` (AC-23)."""
        return f"{PRICES_FILE.name}@{self.date.isoformat()}"


def read_prices_file(path: Path = PRICES_FILE) -> dict[str, Any]:
    """Parse the price file, turning a missing or malformed file into one `AgentError`."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise AgentError(f"The price file {path} is missing") from None
    except OSError as error:
        raise AgentError(f"The price file {path} cannot be read: {error.strerror}") from None
    except tomllib.TOMLDecodeError as error:
        raise AgentError(f"The price file {path} is not valid TOML: {error}") from None


def _problems(entry: Mapping[str, Any]) -> list[str]:
    problems = [f"{key} is not filled" for key in ENTRY_KEYS if key not in entry]
    problems.extend(f"{key} is not a known key" for key in entry if key not in ENTRY_KEYS)
    page = entry.get("page")
    if page is not None and (not isinstance(page, str) or not page.strip()):
        problems.append("page must be the provider page, as a string")
    date = entry.get("date")
    if date is not None and (not isinstance(date, dt.date) or isinstance(date, dt.datetime)):
        problems.append("date must be a TOML date such as 2026-01-31, without quotes")
    for key in PRICE_KEYS:
        value = entry.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            problems.append(f"{key} must be a number of US dollars per million tokens, 0 or more")
    return problems


def prices_for(model: str, data: Mapping[str, Any]) -> Prices:
    """The prices of `model` from the parsed price file; raises `AgentError` naming each gap.

    A model with no entry, or an entry with a key missing or of the wrong type,
    stops the build (AC-23): a run is never priced from a guess.
    """
    entry = data.get(model)
    if not isinstance(entry, Mapping):
        raise AgentError(
            f"{PRICES_FILE.name} has no entry for {model!r}; add one from the provider's"
            " pricing page with its page and date"
        )
    problems = _problems(entry)
    if problems:
        raise AgentError(
            f"The {PRICES_FILE.name} entry for {model!r} is not usable:\n  " + "\n  ".join(problems)
        )
    return Prices(
        page=entry["page"],
        date=entry["date"],
        input=float(entry["input"]),
        output=float(entry["output"]),
        cache_write=float(entry["cache_write"]),
        cache_read=float(entry["cache_read"]),
    )
