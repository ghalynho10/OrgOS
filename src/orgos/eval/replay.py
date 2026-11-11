"""The replay agent: the accepted gold queries run as an agent, free and with no model (AC-34).

It implements `QueryAgent`, so the runner drives it exactly as it drives the
query agent, with no network call and zero usage. It runs the answer file and
the sources files of an accepted pair as pinned in `gold/ACCEPTED`, and reads
no known answer: the file layout comes from the pins alone. A question with no
accepted pair, or a pinned file that no longer matches its hash, is refused.
Its results go under `results/agent_runs/replay/` and never count as agent results.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.frozen import sha256_file
from orgos.eval.gold import ACCEPTED, FOLDERS, GOLD, FileRun, parse_pins, run_files
from orgos.eval.gold_adapters import ADAPTERS
from orgos.eval.handoff import (
    AgentDescription,
    AgentRun,
    QueryAttempt,
    RunOutcome,
    SourceStep,
    Store,
    Usage,
)
from orgos.eval.models import Question

REPLAY = "replay"
ZERO_USAGE = Usage(input_tokens=0, output_tokens=0, requests=0, tool_calls=0, cost_usd=0.0)


def accepted_files(root: Path, question_id: str, store: Store) -> tuple[Path, ...]:
    """The pinned answer file and sources files of an accepted pair, sources in step order."""
    path = root / ACCEPTED
    pins = parse_pins(path.read_text(encoding="utf-8")) if path.is_file() else ()
    folder = FOLDERS[store]
    prefix = f"{GOLD / folder / question_id}."
    pair = {Path(pinned): sha for sha, pinned in pins if pinned.startswith(prefix)}
    answer = GOLD / folder / f"{question_id}.answer.{folder}"
    if answer not in pair:
        raise EvalError(f"{question_id} {store} has no accepted gold pair; replay refuses it")
    changed = [str(p) for p, sha in pair.items() if sha256_file(root / p) != sha]
    if changed:
        raise EvalError(f"{', '.join(changed)} differs from its pin in {ACCEPTED}")
    step = re.compile(rf"{re.escape(question_id)}\.sources\.(\d+)\.{folder}")
    sources = sorted(
        (int(match.group(1)), p) for p in pair if (match := step.fullmatch(p.name)) is not None
    )
    return (answer, *(p for _, p in sources))


def _attempt(run: FileRun, text: str) -> QueryAttempt:
    rows = len(run.result.rows) if run.result is not None else 0
    return QueryAttempt(text=text, outcome=run.outcome, seconds=run.seconds, rows=rows)


def _no_answer(outcome: RunOutcome, attempts: tuple[QueryAttempt, ...], seconds: float) -> AgentRun:
    return AgentRun(
        response_model=None,
        outcome=outcome,
        shape=None,
        value=None,
        sources=(),
        attempts=attempts,
        usage=ZERO_USAGE,
        seconds=seconds,
    )


@dataclass(frozen=True)
class ReplayAgent:
    """The accepted gold queries as a `QueryAgent`."""

    settings: Settings
    root: Path

    def describe(self) -> AgentDescription:
        """The fixed description of replay: no model, no price, version `replay`."""
        return AgentDescription(
            model_id=REPLAY,
            version_hash=REPLAY,
            parts={},
            price_table="none",
            usd_per_million_input=0.0,
            usd_per_million_output=0.0,
        )

    def run(self, question: Question, store: Store) -> AgentRun:
        """Run the accepted pair's answer and sources files and reshape them with its adapter."""
        started = time.monotonic()
        paths = accepted_files(self.root, question.id, store)
        files = tuple((str(p), (self.root / p).read_text(encoding="utf-8")) for p in paths)
        runs = run_files(self.settings, store, files)
        attempts = tuple(_attempt(run, text) for run, (_, text) in zip(runs, files, strict=False))
        if any(run.outcome != "ok" for run in runs):
            # AC-10: a run ending without an answer after a timeout is `store_timeout`.
            outcome: RunOutcome = (
                "store_timeout" if runs[-1].outcome == "store_timeout" else "no_answer"
            )
            return _no_answer(outcome, attempts, time.monotonic() - started)
        results = tuple(run.result for run in runs if run.result is not None)
        try:
            value, steps = ADAPTERS[question.id](results[0], results[1:])
        except (EvalError, KeyError):
            return _no_answer("error", attempts, time.monotonic() - started)
        sources: tuple[SourceStep, ...] = tuple(
            {kind: tuple(str(item) for item in ids) for kind, ids in step.items()} for step in steps
        )
        return AgentRun(
            response_model=None,
            outcome="answered",
            shape=question.shape,
            value=value,
            sources=sources,
            attempts=attempts,
            usage=ZERO_USAGE,
            seconds=time.monotonic() - started,
        )
