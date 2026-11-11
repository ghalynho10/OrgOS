"""The gold pin: every file in `gold/ACCEPTED` equals its hash (spec 0011 AC-20).

`test_the_repo_gold_pins_hold` is the check pre-commit and CI run on this repo.
Editing an accepted query or explain file fails it until the pair is tried and
accepted again.
"""

from pathlib import Path

from orgos.eval.frozen import sha256_file
from orgos.eval.gold import ACCEPTED, pin_problems

ROOT = Path(__file__).resolve().parents[1]


def test_the_repo_gold_pins_hold() -> None:
    assert pin_problems(ROOT) == ()


def test_no_accepted_file_passes(tmp_path: Path) -> None:
    assert pin_problems(tmp_path) == ()


def test_an_edited_or_missing_pinned_file_fails(tmp_path: Path) -> None:
    (tmp_path / "gold/sql").mkdir(parents=True)
    query = tmp_path / "gold/sql/Q01.answer.sql"
    query.write_text("SELECT 1\n")
    (tmp_path / ACCEPTED).write_text(
        f"{sha256_file(query)}  gold/sql/Q01.answer.sql\n{'0' * 64}  gold/sql/Q01.explain.md\n"
    )
    assert pin_problems(tmp_path) == (
        "gold/sql/Q01.explain.md is pinned in gold/ACCEPTED but missing",
    )
    query.write_text("SELECT 2\n")
    assert pin_problems(tmp_path)[0].startswith("gold/sql/Q01.answer.sql differs from its hash")
