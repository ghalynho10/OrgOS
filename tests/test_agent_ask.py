"""`orgos ask` on a scripted agent (spec 0012 AC-5, AC-26).

The agent is the real one on a `FunctionModel` with fake executors; the
preflight is replaced by a recorder. Run files live in a temporary folder, never
in the repository's `results/`.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orgos.agent.ask import render_run
from orgos.agent.errors import AgentError
from orgos.cli import app, ask_lines
from orgos.config import load_settings
from orgos.eval.handoff import AgentRun, QueryAttempt, StoreOutage, Usage
from tests.test_agent_run import SET_ANSWER, _agent, _scripted

SETTINGS = load_settings(agent_token_limit=400_000)


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def test_ask_refuses_without_go_after_printing_the_bound(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    checked: list[Path] = []
    agent = _agent(_scripted())
    caplog.set_level(logging.INFO)
    with pytest.raises(AgentError, match="add --go"):
        ask_lines(
            SETTINGS,
            tmp_path,
            "Which issues exist?",
            "sql",
            go=False,
            check=lambda settings, root: checked.append(root),
            build=lambda settings: agent,
        )
    assert checked == [tmp_path]
    assert "upper bound for one run: $4.0000" in caplog.text
    assert "running" not in caplog.text


def test_ask_runs_once_prints_answer_sources_queries_and_writes_no_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    agent = _agent(_scripted("SELECT issue_key AS id FROM model.issue", SET_ANSWER))
    before = _files(tmp_path)
    caplog.set_level(logging.INFO)
    lines = ask_lines(
        SETTINGS,
        tmp_path,
        "Which issues exist?",
        "sql",
        go=True,
        check=lambda settings, root: None,
        build=lambda settings: agent,
    )
    assert lines[0].startswith('answer (set): ["issue:CLOUD-0", "issue:CLOUD-1"')
    assert lines[1] == "sources: 1 steps, 60 IDs"
    assert lines[3] == "1 | issue | 60 | CLOUD-0, CLOUD-1, CLOUD-2, CLOUD-3, CLOUD-4 ..."
    assert lines[4] == "queries: 1"
    assert lines[5].startswith("1. ok, 60 rows,")
    assert lines[6] == "   SELECT issue_key AS id FROM model.issue"
    assert caplog.text.index("upper bound") < caplog.text.index("running on sql")
    assert _files(tmp_path) == before


def test_ask_prints_the_measured_mean_of_finished_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    agent = _agent(_scripted())
    folder = tmp_path / "results/agent_runs" / agent.describe().version_hash
    folder.mkdir(parents=True)
    for number, cost in ((1, 0.10), (2, 0.30)):
        (folder / f"Q01-sql-{number}.json").write_text(json.dumps({"usage": {"cost_usd": cost}}))
    caplog.set_level(logging.INFO)
    with pytest.raises(AgentError):
        ask_lines(
            SETTINGS,
            tmp_path,
            "x",
            "graph",
            go=False,
            check=lambda settings, root: None,
            build=lambda settings: agent,
        )
    assert "measured mean cost of 2 finished runs of this version: $0.2000" in caplog.text


# covers: review 2026-11-11 Minor 1, the stop message of `orgos ask` prints the run's cost
def test_a_store_outage_in_ask_prints_the_cost_and_keeps_its_type(tmp_path: Path) -> None:
    from orgos.sqlstore.query import SqlConnectionLost
    from tests.test_agent_run import _executor

    sql = _executor(SqlConnectionLost("lost the connection to Postgres"))
    agent = _agent(_scripted("SELECT 1"), sql=sql)
    with pytest.raises(
        StoreOutage, match=r"^lost the connection to Postgres\. It cost \$0\.0"
    ) as raised:
        ask_lines(
            SETTINGS,
            tmp_path,
            "x",
            "sql",
            go=True,
            check=lambda settings, root: None,
            build=lambda settings: agent,
        )
    assert raised.value.cost_usd > 0


def test_a_bad_store_is_refused_before_any_check(tmp_path: Path) -> None:
    from orgos.eval import EvalError

    with pytest.raises(EvalError, match="--store must be sql or graph"):
        ask_lines(
            SETTINGS,
            tmp_path,
            "x",
            "mongo",
            go=True,
            check=lambda settings, root: pytest.fail("checked"),
            build=lambda settings: pytest.fail("built"),
        )


def test_no_answer_prints_why_and_no_sources() -> None:
    run = AgentRun(
        response_model=None,
        outcome="limit_hit",
        shape=None,
        value=None,
        sources=(),
        attempts=(QueryAttempt("MATCH (n)\nRETURN count(n) AS value", "store_timeout", 30.0, 0),),
        usage=Usage(0, 0, 0, 1, 0.0),
        seconds=31.0,
    )
    assert render_run(run) == (
        "answer: none (limit_hit)",
        "sources: 0 steps, 0 IDs",
        "queries: 1",
        "1. store_timeout, 0 rows, 30.00 s:",
        "   MATCH (n)",
        "   RETURN count(n) AS value",
    )


def test_help_needs_no_env_and_does_not_load_the_model_library(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["ask", "--help"])
    assert result.exit_code == 0
    assert "--go" in result.output
    loaded = subprocess.run(
        [sys.executable, "-c", "import orgos.cli, sys; print('pydantic_ai' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    assert loaded.stdout.strip() == "False"
