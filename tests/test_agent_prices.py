"""The price table reader and the agent's exit code (spec 0012 AC-20, AC-23)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import pytest

from orgos.agent.errors import AgentError
from orgos.agent.models import PRICES_FILE, Prices, prices_for, read_prices_file
from orgos.cli import EXIT_CODES

ROOT = Path(__file__).resolve().parents[1]
MODEL = "anthropic:test-model"
FILLED: dict[str, Any] = {
    "page": "https://example.invalid/pricing",
    "date": dt.date(2026, 1, 31),
    "input": 3,
    "output": 15.0,
    "cache_write": 3.75,
    "cache_read": 0.3,
}


def test_a_filled_entry_gives_its_four_prices_and_the_table_name() -> None:
    prices = prices_for(MODEL, {MODEL: FILLED})
    assert prices == Prices(
        page="https://example.invalid/pricing",
        date=dt.date(2026, 1, 31),
        input=3.0,
        output=15.0,
        cache_write=3.75,
        cache_read=0.3,
    )
    assert prices.table == "prices.toml@2026-01-31"


def test_a_model_with_no_entry_stops_the_build() -> None:
    with pytest.raises(AgentError, match="has no entry for 'anthropic:other'"):
        prices_for("anthropic:other", {MODEL: FILLED})


def test_an_entry_left_as_its_structure_names_every_key_to_fill() -> None:
    with pytest.raises(AgentError) as error:
        prices_for(MODEL, {MODEL: {}})
    for key in ("page", "date", "input", "output", "cache_write", "cache_read"):
        assert f"{key} is not filled" in str(error.value)


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("input", -1, "input must be a number"),
        ("output", "15", "output must be a number"),
        ("cache_read", True, "cache_read must be a number"),
        ("date", "2026-01-31", "date must be a TOML date"),
        ("date", dt.datetime(2026, 1, 31, 12), "date must be a TOML date"),
        ("page", " ", "page must be the provider page"),
        ("currency", "USD", "currency is not a known key"),
    ],
)
def test_a_bad_value_is_named(key: str, value: object, reason: str) -> None:
    with pytest.raises(AgentError, match=reason):
        prices_for(MODEL, {MODEL: {**FILLED, key: value}})


def test_the_shipped_file_parses_and_has_an_entry_for_the_example_model() -> None:
    example = (ROOT / ".env.example").read_text()
    model = re.search(r"^ORGOS_MODEL=(.+)$", example, re.MULTILINE)
    assert model is not None
    assert model.group(1) in read_prices_file(PRICES_FILE)


def test_a_broken_price_file_is_one_agent_error(tmp_path: Path) -> None:
    broken = tmp_path / "prices.toml"
    broken.write_text("[x\n")
    with pytest.raises(AgentError, match="not valid TOML"):
        read_prices_file(broken)
    with pytest.raises(AgentError, match="is missing"):
        read_prices_file(tmp_path / "absent.toml")


def test_agent_error_has_its_exit_code() -> None:
    assert (AgentError, 7) in EXIT_CODES
