"""The one query agent, the same for both stores. Provider code lives in `orgos.agent.models`.

`build_agent` is what the runner loads as `orgos.agent:build_agent` (spec 0011
AC-38, spec 0012 AC-1). It is looked up on first use, so importing this package
for `AgentError` (the CLI's exit codes) does not load the model library.
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_agent"]


def __getattr__(name: str) -> Any:
    """`build_agent`, imported from `orgos.agent.agent` when first asked for."""
    if name == "build_agent":
        from orgos.agent.agent import build_agent

        return build_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
