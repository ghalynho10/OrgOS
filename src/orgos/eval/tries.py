"""The gold tries log and the rules read from it (spec 0011 AC-15 to AC-17, AC-19).

`results/gold/tries.jsonl` is append only: `gold try`, `gold decide` and
`gold accept` each add one line and nothing rewrites one. The state of a pair
(its round, this round's tries, a recorded discrepancy) is worked out from the
log alone. From the second try of a round a try needs a hypothesis and one
citation of the reading note or the schema, so a retry cannot rest on the
counts alone. After 3 failed tries in a round a fourth is refused until
`gold decide` opens a new round or records a discrepancy. The rules are pure;
`read_tries` and `append` are the only functions that touch the file.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orgos.eval import EvalError
from orgos.eval.frozen import git
from orgos.graphstore.mapping import GraphMapping

TRIES = Path("results/gold/tries.jsonl")
MAX_FAILED = 3
DBT_MODEL_YAML = Path("dbt/models/model/model.yml")
# Uncommitted changes here make a record dirty (AC-22).
DIRTY_PATHS = ("src/orgos", "gold")
_CITED_STEP = re.compile(r"step (\d+)")
# The `- name:` lines of the model YAML: its 7 models and their columns.
_YAML_NAME = re.compile(r"^\s*- name: (\w+)\s*$", re.MULTILINE)


def read_tries(root: Path) -> tuple[Mapping[str, Any], ...]:
    """Every line of the tries log, oldest first; none when the log does not exist yet."""
    path = root / TRIES
    if not path.is_file():
        return ()
    try:
        return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    except json.JSONDecodeError as error:
        raise EvalError(f"{TRIES} holds a line that is not JSON: {error}") from None


def append(root: Path, line: Mapping[str, Any]) -> None:
    """Add one line at the end of the tries log; never rewrites an earlier line."""
    path = root / TRIES
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def now() -> str:
    """The UTC time of an event, to the second."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def git_record(root: Path) -> dict[str, Any]:
    """The commit and whether `src/orgos/` or `gold/` hold uncommitted changes (AC-22)."""
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain", "--", *DIRTY_PATHS)
    if head.returncode != 0 or status.returncode != 0:
        raise EvalError(f"git failed: {(head.stderr or status.stderr).strip()}")
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


@dataclass(frozen=True)
class PairState:
    """One question and store as the log tells it: its round, this round's tries, a discrepancy."""

    round: int
    tries: tuple[Mapping[str, Any], ...]
    discrepancy: str | None

    @property
    def failed(self) -> int:
        """The tries of this round that were not correct, a literal refusal included."""
        return sum(1 for line in self.tries if not line["correct"])

    @property
    def blocked(self) -> bool:
        """True after 3 failed tries in this round (AC-17)."""
        return self.failed >= MAX_FAILED

    @property
    def next_try(self) -> int:
        """The number the next try of this round gets."""
        return len(self.tries) + 1

    @property
    def latest(self) -> Mapping[str, Any] | None:
        """The latest try of this round, or None before the first."""
        return self.tries[-1] if self.tries else None


def pair_state(lines: Iterable[Mapping[str, Any]], question: str, store: str) -> PairState:
    """The state of one pair: each `--rewrite` opens a round, `--discrepancy` closes the pair."""
    round_number = 1
    tries: list[Mapping[str, Any]] = []
    discrepancy = None
    for line in lines:
        if line.get("question") != question or line.get("store") != store:
            continue
        if line["event"] == "try" and line["round"] == round_number:
            tries.append(line)
        elif line["event"] == "decide" and line["decision"] == "rewrite":
            round_number += 1
            tries = []
        elif line["event"] == "decide" and line["decision"] == "discrepancy":
            discrepancy = str(line["text"])
    return PairState(round=round_number, tries=tuple(tries), discrepancy=discrepancy)


def latest_correct(state: PairState) -> Mapping[str, Any] | None:
    """The latest correct try of the pair's current round, or None."""
    return next((line for line in reversed(state.tries) if line["correct"]), None)


def load_id_problems(
    lines: Iterable[Mapping[str, Any]], question: str, store: str, load_id: str
) -> tuple[str, ...]:
    """Every try of the pair, in any round, holding a load ID other than L (AC-37)."""
    held = sorted(
        {
            str(line.get("load_id"))
            for line in lines
            if line.get("question") == question
            and line.get("store") == store
            and line.get("event") == "try"
            and line.get("load_id") != load_id
        }
    )
    return tuple(
        f"{question} {store}: a try holds load ID {other}, the current load ID is {load_id}"
        for other in held
    )


def reading_steps(reading: str) -> tuple[int, ...]:
    """The numbered source steps of a reading note: each `(n)` of its `Sources` line."""
    for line in reading.splitlines():
        if line.startswith("Sources, step by step:"):
            return tuple(int(number) for number in re.findall(r"\((\d+)\)", line))
    return ()


def schema_names(root: Path, mapping: GraphMapping) -> frozenset[str]:
    """The tables and columns of the dbt `model` YAML; the mapping's labels, types, properties."""
    try:
        text = (root / DBT_MODEL_YAML).read_text(encoding="utf-8")
    except OSError as error:
        raise EvalError(f"cannot read {DBT_MODEL_YAML}: {error.strerror}") from None
    names = set(_YAML_NAME.findall(text))
    for node in mapping.nodes:
        names.update((node.label, node.key.property, node.key.column))
        names.update(name for column in node.columns for name in (column.property, column.column))
    for relationship in mapping.relationships:
        names.update(relationship.relationship_types)
        names.update(
            name for column in relationship.columns for name in (column.property, column.column)
        )
    return frozenset(names)


def citation_problem(
    hypothesis: str | None, cites: str | None, steps: tuple[int, ...], names: frozenset[str]
) -> str | None:
    """Why a retry's hypothesis and citation are refused (AC-16), or None when they hold."""
    if not hypothesis or not hypothesis.strip():
        return "from the second try of a round, --hypothesis is required"
    if not cites or not cites.strip():
        return "from the second try of a round, --cites is required"
    cited = cites.strip()
    match = _CITED_STEP.fullmatch(cited)
    if match is not None:
        if int(match.group(1)) in steps:
            return None
        shown = ", ".join(str(step) for step in steps) or "none"
        return f"--cites {cited}: the reading note numbers its source steps {shown}"
    if cited in names:
        return None
    return (
        f"--cites {cited}: not `step <n>` for a numbered source step of the reading note,"
        " nor a table, column, label, relationship type or property name of the dbt YAML"
        " or mapping.toml"
    )
