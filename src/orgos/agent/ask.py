"""What `orgos ask` prints for one plain question run (spec 0012 AC-26).

Pure: the answer on one plain line, then the sources as a short table with
their count, then each query the agent ran, in call order. The run is not
scored and nothing is written; the command in `orgos.cli` runs the preflight,
builds the agent and prints these lines to stdout.
"""

from __future__ import annotations

import json
from typing import Any

from orgos.eval.handoff import AgentRun

SHOWN_IDS = 5


def _plain(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def answer_line(run: AgentRun) -> str:
    """The answer on one line: the outcome, the shape and the value, or why there is none."""
    if run.shape is None:
        return f"answer: none ({run.outcome})"
    return f"answer ({run.shape}): {_plain(run.value)}"


def source_lines(run: AgentRun) -> list[str]:
    """The sources as a short table: step, kind, count and the first few IDs."""
    total = sum(len(ids) for step in run.sources for ids in step.values())
    lines = [f"sources: {len(run.sources)} steps, {total} IDs"]
    if run.sources:
        lines.append("step | kind | count | first IDs")
        for number, step in enumerate(run.sources, start=1):
            for kind, ids in sorted(step.items()):
                shown = ", ".join(ids[:SHOWN_IDS]) + (" ..." if len(ids) > SHOWN_IDS else "")
                lines.append(f"{number} | {kind} | {len(ids)} | {shown}")
    return lines


def query_lines(run: AgentRun) -> list[str]:
    """Each query in call order with its outcome, row count and seconds, then its text."""
    lines = [f"queries: {len(run.attempts)}"]
    for number, attempt in enumerate(run.attempts, start=1):
        lines.append(f"{number}. {attempt.outcome}, {attempt.rows} rows, {attempt.seconds:.2f} s:")
        lines.extend(f"   {line}" for line in attempt.text.splitlines())
    return lines


def render_run(run: AgentRun) -> tuple[str, ...]:
    """Everything `orgos ask` prints to stdout for one run."""
    return (answer_line(run), *source_lines(run), *query_lines(run))


def usage_line(run: AgentRun) -> str:
    """One progress line with what the run used and cost."""
    usage = run.usage
    return (
        f"used {usage.requests} requests, {usage.tool_calls} queries,"
        f" {usage.input_tokens:,} input and {usage.output_tokens:,} output tokens,"
        f" ${usage.cost_usd:.4f}, {run.seconds:.1f} s; model {run.response_model}"
    )
