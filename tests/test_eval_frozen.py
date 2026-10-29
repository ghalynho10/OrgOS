"""The freeze test (spec 0005, AC-2, AC-3, AC-46, AC-47).

`test_the_repo_eval_set_is_unwritten_or_frozen` is the check pre-commit and CI
run on this repo; it never skips. The others drive the same rule on a
temporary git repo.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from orgos.eval.frozen import (
    AMENDMENTS,
    ANSWERS,
    FROZEN,
    QUESTIONS,
    frozen_line,
    frozen_problems,
    tagged_frozen,
)

ROOT = Path(__file__).resolve().parents[1]


def test_the_repo_eval_set_is_unwritten_or_frozen() -> None:
    assert frozen_problems(ROOT, tagged_frozen(ROOT)) == ()


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "tag.gpgsign", "false")
    (tmp_path / "eval").mkdir()
    return tmp_path


def write(root: Path, path: str, text: str) -> None:
    (root / path).write_text(text)


def pin(root: Path, *paths: str) -> None:
    write(root, FROZEN, "".join(frozen_line(root, path) + "\n" for path in paths))


def test_both_absent_passes(repo: Path) -> None:
    assert frozen_problems(repo, tagged_frozen(repo)) == ()


def test_questions_without_frozen_fails(repo: Path) -> None:
    write(repo, QUESTIONS, "[[question]]\n")
    assert frozen_problems(repo, None) == (f"{QUESTIONS} exists without {FROZEN}",)


@pytest.mark.parametrize("path", [ANSWERS, AMENDMENTS])
def test_answers_or_amendments_without_frozen_fail(repo: Path, path: str) -> None:
    write(repo, path, "{}\n")
    assert frozen_problems(repo, None) == (f"{path} exists without {FROZEN}",)


def test_pinned_questions_pass_and_shasum_agrees(repo: Path) -> None:
    write(repo, QUESTIONS, "[[question]]\n")
    pin(repo, QUESTIONS)
    assert frozen_problems(repo, None) == ()
    command = ["shasum", "-a", "256", "-c", FROZEN]
    if shutil.which("shasum") is None:
        command = ["sha256sum", "-c", FROZEN]
    subprocess.run(command, cwd=repo, check=True, capture_output=True)


def test_an_edited_listed_file_fails(repo: Path) -> None:
    write(repo, QUESTIONS, "[[question]]\n")
    pin(repo, QUESTIONS)
    write(repo, QUESTIONS, "[[question]]\nid = 1\n")
    assert frozen_problems(repo, None) == (f"{QUESTIONS} differs from its hash in {FROZEN}",)


def test_a_missing_listed_file_fails(repo: Path) -> None:
    write(repo, QUESTIONS, "[[question]]\n")
    write(repo, ANSWERS, "{}\n")
    pin(repo, QUESTIONS, ANSWERS)
    (repo / ANSWERS).unlink()
    assert frozen_problems(repo, None) == (f"{ANSWERS} is listed in {FROZEN} but missing",)


def test_frozen_without_a_questions_line_fails(repo: Path) -> None:
    write(repo, QUESTIONS, "[[question]]\n")
    write(repo, ANSWERS, "{}\n")
    pin(repo, ANSWERS)
    assert frozen_problems(repo, None) == (f"{FROZEN} does not list {QUESTIONS}",)


@pytest.mark.parametrize(
    "line",
    ["abc  eval/questions.toml", "0" * 64 + " eval/questions.toml", "0" * 64 + "  /etc/hosts"],
)
def test_a_malformed_line_fails(repo: Path, line: str) -> None:
    write(repo, QUESTIONS, "[[question]]\n")
    pin(repo, QUESTIONS)
    write(repo, FROZEN, (repo / FROZEN).read_text() + line + "\n")
    assert any(problem.startswith(f"{FROZEN} line 2") for problem in frozen_problems(repo, None))


def test_frozen_differing_from_the_tagged_copy_fails(repo: Path) -> None:
    write(repo, QUESTIONS, "[[question]]\n")
    write(repo, ANSWERS, "{}\n")
    pin(repo, QUESTIONS, ANSWERS)
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "freeze")
    git(repo, "tag", "--annotate", "-m", "freeze", "eval-frozen")
    assert frozen_problems(repo, tagged_frozen(repo)) == ()

    pin(repo, QUESTIONS)  # FROZEN drifts: the answers line is dropped
    assert frozen_problems(repo, tagged_frozen(repo)) == (
        f"{FROZEN} differs from its copy at the eval-frozen tag",
    )


def test_no_tag_means_no_tagged_copy(repo: Path) -> None:
    assert tagged_frozen(repo) is None


def test_both_ci_checkout_steps_fetch_tags() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    checkouts = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/checkout")
    ]
    assert len(checkouts) == 2
    assert all(step.get("with", {}).get("fetch-tags") is True for step in checkouts)
