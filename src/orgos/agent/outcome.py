"""How a run ended, mapped to exactly one outcome (spec 0012 AC-21).

The agent records how the model loop stopped as one `RunEnd`, and
`run_outcome` maps it with the attempts to the outcome the runner scores. Pure,
so each branch has its own test. A provider 4xx, a provider outage and a lost
store are not ends: they raise and stop the command.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from orgos.eval.handoff import QueryAttempt, RunOutcome

# How the loop stopped: `final_answer` passed the validator; the model ended in
# plain text; a failed model turn (`UnexpectedModelBehavior`: retries used up,
# `max_tokens` truncation, a malformed reply); a request, tool call or token
# limit; an unexpected exception in our own code, caught inside the run.
RunEnd = Literal["final_answer", "plain_text", "model_failure", "usage_limit", "own_error"]


def run_outcome(end: RunEnd, attempts: Sequence[QueryAttempt]) -> RunOutcome:
    """The one outcome of a run.

    A limit is named even when the last attempt timed out, because the limit is
    what stopped the run. A run that timed out once and then answered is
    `answered`; one that ended without an answer after a timed out last attempt
    is `store_timeout`.
    """
    if end == "final_answer":
        return "answered"
    if end == "usage_limit":
        return "limit_hit"
    if end == "own_error":
        return "error"
    if attempts and attempts[-1].outcome == "store_timeout":
        return "store_timeout"
    return "no_answer"
