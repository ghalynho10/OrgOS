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

from orgos.agent.errors import AgentError

PRICES_FILE = Path(__file__).with_name("prices.toml")
PRICE_KEYS = ("input", "output", "cache_write", "cache_read")
ENTRY_KEYS = ("page", "date", *PRICE_KEYS)


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
