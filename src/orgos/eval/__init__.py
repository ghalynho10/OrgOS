"""The eval set, its known answers, and (later) the runner that scores both stores.

The 20 questions and their reading notes are written by hand in `eval/` and
pinned by `eval/FROZEN` before any answer exists (spec 0005). This package
holds the tools around them: the data description, the question checker, the
entry rebuild, the comparison rule, and later the known answer code over raw
Mongo and the runner (feature 12). Every failure it can expect surfaces as one
`EvalError`, which the CLI turns into exit code 4.
"""

from __future__ import annotations


class EvalError(Exception):
    """Raised when the eval set breaks a rule, a guard refuses, or its data cannot be read."""
