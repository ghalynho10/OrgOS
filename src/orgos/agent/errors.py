"""The one named error of the query agent (spec 0012).

A refusal to build (a session check that fails, a model with no price entry),
a broken input file, or a provider answer that rules out every run (a 400,
401, 403 or 404) is an `AgentError`. The CLI turns it into exit code 7. It is
not a run outcome: the command stops with its message instead of writing a
file per run (AC-21). The provider's refusal is a `ProviderRefusal`, an
`AgentError` that also carries what the run cost before it (AC-23).
"""

from __future__ import annotations

from orgos.eval.handoff import RunStopped


class AgentError(Exception):
    """Raised when the agent cannot be built or cannot run at all."""


class ProviderRefusal(AgentError, RunStopped):
    """The provider refused a request partway through a run; exit code 7 as an `AgentError`."""
