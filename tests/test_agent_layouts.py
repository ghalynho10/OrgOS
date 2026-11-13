"""Each layout line against the accepted gold files, by column names only (spec 0015 AC-4).

A step's kinds are the `AS <kind>` aliases of its gold sources file
(`gold/sql/Qnn.sources.<k>.sql`), or of the answer file for a path question,
whose list columns are its hop kinds. Kinds are compared as sets per step, in
step order, which is the rule of `orgos.eval.compare.source_match`. The test
reads no value, no file under `eval/` and no run file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from orgos.agent.agent import read_layouts, read_questions

ROOT = Path(__file__).resolve().parents[1]
GOLD_SQL = ROOT / "gold/sql"
KIND_ALIAS = re.compile(
    r"\bAS\s+(issue|project|person|time_entry|issue_link|parent)\b", re.IGNORECASE
)
# Zero guarded questions with no sources file: their lines are the engineer's rulings (AC-5).
EXEMPT = ("Q03", "Q16")
QUESTIONS = read_questions(ROOT)
LAYOUTS = read_layouts(QUESTIONS)
SHAPES = {question.id: question.shape for question in QUESTIONS}


def _kinds(path: Path) -> frozenset[str]:
    kinds = frozenset(k.lower() for k in KIND_ALIAS.findall(path.read_text(encoding="utf-8")))
    assert kinds, f"{path.name} names no source kind"
    return kinds


def _gold_steps(qid: str) -> tuple[frozenset[str], ...]:
    if SHAPES[qid] == "path":
        return (_kinds(GOLD_SQL / f"{qid}.answer.sql"),)
    files = sorted(
        GOLD_SQL.glob(f"{qid}.sources.*.sql"), key=lambda path: int(path.name.split(".")[2])
    )
    return tuple(_kinds(path) for path in files)


@pytest.mark.parametrize("qid", sorted(q for q in LAYOUTS if q not in EXEMPT))
def test_each_layout_equals_the_kinds_of_its_gold_files(qid: str) -> None:
    gold = _gold_steps(qid)
    assert gold, f"{qid} has no gold sources file"
    assert tuple(frozenset(step) for step in LAYOUTS[qid]) == gold


def test_only_the_two_exempt_questions_have_no_sources_file() -> None:
    without = sorted(
        qid
        for qid in LAYOUTS
        if SHAPES[qid] != "path" and not any(GOLD_SQL.glob(f"{qid}.sources.*.sql"))
    )
    assert without == list(EXEMPT)
