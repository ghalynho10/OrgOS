"""The one named error of the query agent (spec 0012).

A refusal to build (a session check that fails, a model with no price entry),
a broken input file, or a provider answer that rules out every run (a 400,
401, 403 or 404) is an `AgentError`. The CLI turns it into exit code 7. It is
not a run outcome: the command stops with its message instead of writing a
file per run (AC-21).
"""

from __future__ import annotations


class AgentError(Exception):
    """Raised when the agent cannot be built or cannot run at all."""
