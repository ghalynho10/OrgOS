"""Unit tests for the answer guards and the corpus snapshot (spec 0005, AC-4, AC-6, AC-19,
AC-21, AC-24, AC-26, AC-27, AC-41, AC-43).

The file guards run on a temporary git repo with a bare repo as its remote;
uniqueness and reconciliation run on synthetic documents. No store.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.frozen import AMENDMENTS, ANSWERS, FROZEN, QUESTIONS, frozen_line
from orgos.eval.guards import (
    Targets,
    entry_mismatches,
    file_guards,
    reconciliation_checks,
    require,
    uniqueness_checks,
)
from orgos.eval.rules import IssueLink, Parent
from orgos.eval.snapshot import build_snapshot, corpus_projection, issue_from

# File guards: frozen refusal, hashes, pushed checks, in the AC-43 order


@pytest.fixture(autouse=True)
def no_hook_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Under a pre-commit hook git sets GIT_INDEX_FILE and friends for the outer repo.
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    remote, root = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "--quiet", "--bare", str(remote))
    git(tmp_path, "init", "--quiet", "--initial-branch=main", str(root))
    for key, value in (
        ("user.email", "test@example.invalid"),
        ("user.name", "test"),
        ("commit.gpgsign", "false"),
    ):
        git(root, "config", key, value)
    git(root, "remote", "add", "origin", str(remote))
    (root / "eval").mkdir()
    return root


def write(root: Path, path: str, text: str) -> None:
    (root / path).write_text(text)


def pin(root: Path, *paths: str) -> None:
    write(root, FROZEN, "".join(frozen_line(root, path) + "\n" for path in paths))


def commit(root: Path, message: str, *, push: bool) -> None:
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", message)
    if push:
        git(root, "push", "--quiet", "-u", "origin", "main")


def step1(root: Path, *, push: bool = True) -> None:
    write(root, QUESTIONS, "[[question]]\n")
    pin(root, QUESTIONS)
    commit(root, "step 1", push=push)


def test_a_pushed_step1_passes_and_names_its_branch(repo: Path) -> None:
    step1(repo)
    hashes, pushed = file_guards(repo, check=False)
    assert hashes.amendments is None
    assert pushed.step1_branches == ("origin/main",)
    assert pushed.step1_commit == git(repo, "rev-parse", "HEAD").strip()


def test_the_frozen_refusal_comes_first_and_check_skips_it(repo: Path) -> None:
    step1(repo, push=False)
    write(repo, ANSWERS, "{}\n")
    pin(repo, QUESTIONS, ANSWERS)
    write(repo, QUESTIONS, "[[question]]\nid = 1\n")  # the hash now differs too
    with pytest.raises(EvalError, match=r"^frozen refusal"):
        file_guards(repo, check=False)
    with pytest.raises(EvalError, match=r"^hashes"):
        file_guards(repo, check=True)


def test_a_differing_questions_hash_refuses_before_the_pushed_checks(repo: Path) -> None:
    step1(repo, push=False)
    write(repo, QUESTIONS, "[[question]]\nid = 1\n")
    with pytest.raises(EvalError, match=rf"^hashes: {QUESTIONS}"):
        file_guards(repo, check=False)


def test_an_amendment_missing_from_frozen_refuses(repo: Path) -> None:
    step1(repo)
    write(repo, AMENDMENTS, "[[amendment]]\n")
    with pytest.raises(EvalError, match=rf"^hashes: {AMENDMENTS}"):
        file_guards(repo, check=False)


def test_a_step1_on_no_remote_branch_refuses(repo: Path) -> None:
    step1(repo, push=False)
    with pytest.raises(EvalError, match=r"^pushed checks: the step 1 commit"):
        file_guards(repo, check=False)


def test_an_unpushed_amendment_refuses(repo: Path) -> None:
    step1(repo)
    write(repo, AMENDMENTS, "[[amendment]]\n")
    pin(repo, QUESTIONS, AMENDMENTS)
    commit(repo, "amend", push=False)
    with pytest.raises(EvalError, match=r"^pushed checks: the amendment commit"):
        file_guards(repo, check=False)
    git(repo, "push", "--quiet", "origin", "main")
    assert file_guards(repo, check=False)[1].amendment_branches == ("origin/main",)


def test_uncommitted_pinned_files_refuse(repo: Path) -> None:
    step1(repo)
    write(repo, QUESTIONS, "[[question]]\nid = 1\n")
    pin(repo, QUESTIONS)  # hashes agree, but nothing of this is committed
    with pytest.raises(EvalError, match=r"^pushed checks: uncommitted changes"):
        file_guards(repo, check=False)


# The snapshot


AUTHOR = {"key": "u1", "displayName": "U One"}


def doc(key: str, *histories: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {"project": {"key": key.split("-")[0], "id": key.split("-")[0]}, **fields},
        "changelog": {"histories": list(histories)},
    }


def spent(history_id: str, amount: int, author: dict[str, Any] = AUTHOR) -> dict[str, Any]:
    return {
        "id": history_id,
        "author": author,
        "created": "2020-01-01T00:00:00.000+0000",
        "items": [{"field": "timespent", "fieldtype": "jira", "from": None, "to": str(amount)}],
    }


def snapshot(*docs: dict[str, Any], flagged: frozenset[str] = frozenset()) -> Any:
    return build_snapshot(("A", "B"), "key", flagged, docs, {"fields.assignee.key"})


def test_an_issue_keeps_only_the_cited_paths_with_empty_values_dropped() -> None:
    issue = issue_from(
        doc("A-1", assignee={"key": ""}, priority={"name": "High"}), {"fields.assignee.key"}, "key"
    )
    assert issue.cited == (("fields.assignee.key", ()),)
    with pytest.raises(EvalError, match=r"fields\.priority\.name"):
        issue.values("fields.priority.name")


def test_the_projection_drops_array_markers_and_keeps_the_history_members() -> None:
    projection = corpus_projection(
        ["fields.components[].name", "changelog.items[field=status].fromString/toString"]
    )
    assert projection["fields.components.name"] == 1
    assert projection["changelog.histories.items.toString"] == 1
    assert not any("[" in path for path in projection)


def test_the_snapshot_keeps_corpus_links_once_and_corpus_parents() -> None:
    link_a = {"id": "9", "outwardIssue": {"key": "B-1"}}
    link_b = {"id": "9", "inwardIssue": {"key": "A-1"}}
    got = snapshot(
        doc("A-1", issuelinks=[link_a, {"id": "10", "outwardIssue": {"key": "OUT-1"}}]),
        doc("B-1", issuelinks=[link_b], parent={"key": "A-1"}),
        doc("B-2", parent={"key": "OUT-2"}),
    )
    assert got.links == (IssueLink("9", None, "A-1", "B-1"),)
    assert got.parents == (Parent("B-1", "A-1"),)


# Uniqueness (AC-26)


def test_a_duplicated_history_id_stops_with_its_kind_named() -> None:
    got = snapshot(doc("A-1", spent("5", 60)), doc("A-2", spent("5", 60)))
    with pytest.raises(
        EvalError, match=r"^uniqueness: changelog history id once failed: found \['5'\]"
    ):
        require("uniqueness", uniqueness_checks(got.issues))


def test_a_duplicated_issue_key_stops() -> None:
    got = snapshot(doc("A-1"), doc("A-1"))
    with pytest.raises(EvalError, match="issue key on one document"):
        require("uniqueness", uniqueness_checks(got.issues))


def test_a_link_id_on_two_issue_pairs_stops() -> None:
    got = snapshot(
        doc("A-1", issuelinks=[{"id": "9", "outwardIssue": {"key": "B-1"}}]),
        doc("A-2", issuelinks=[{"id": "9", "outwardIssue": {"key": "B-2"}}]),
    )
    with pytest.raises(EvalError, match=r"issue link id to one pair failed: found \['9'\]"):
        require("uniqueness", uniqueness_checks(got.issues))


def test_a_person_key_with_two_names_is_a_note_only() -> None:
    renamed = {"key": "u1", "displayName": "Other"}
    checks = uniqueness_checks(snapshot(doc("A-1", spent("1", 60), spent("2", 60, renamed))).issues)
    require("uniqueness", checks)  # does not stop
    note = checks[-1]
    assert (note.name, note.ok, note.stops, note.found) == (
        "person key to one name",
        False,
        False,
        ["u1"],
    )


# Reconciliation (AC-27, AC-21)


def targets(**changes: Any) -> Targets:
    base: dict[str, Any] = {
        "positive_seconds": {"A": 60, "B": 0},
        "people": {"A": 1, "B": 0},
        "mismatches": (),
        "flagged": (),
        "person_field": "key",
    }
    return Targets(**{**base, **changes})


def test_matching_numbers_pass() -> None:
    checks = reconciliation_checks(snapshot(doc("A-1", spent("1", 60), timespent=60)), targets())
    require("reconciliation", checks)
    assert all(check.ok for check in checks)


def test_a_one_second_difference_stops() -> None:
    got = snapshot(doc("A-1", spent("1", 61), timespent=61))
    with pytest.raises(EvalError, match=r"^reconciliation: positive seconds per project failed"):
        require("reconciliation", reconciliation_checks(got, targets()))


def test_a_different_person_field_or_flag_set_stops() -> None:
    got = snapshot(doc("A-1", spent("1", 60), timespent=60))
    with pytest.raises(EvalError, match="person id field"):
        require("reconciliation", reconciliation_checks(got, targets(person_field="accountId")))
    with pytest.raises(EvalError, match="flagged accounts"):
        require("reconciliation", reconciliation_checks(got, targets(flagged=("bot",))))


def test_the_mismatch_population() -> None:
    bot = {"key": "bot", "displayName": "Bot"}
    got = snapshot(
        doc("A-1", spent("1", 60), timespent=120),  # differs
        doc("A-2", timespent=None),  # no entries, null timespent: outside the population
        doc("A-3", timespent=30),  # no entries, timespent above 0: differs
        doc("A-4", spent("2", 60), spent("3", 60, bot), timespent=0),  # a flagged entry: outside
        doc("A-5", spent("4", 60), spent("5", -60), timespent=None),  # nets to 0, null is 0: same
        flagged=frozenset({"bot"}),
    )
    assert entry_mismatches(got) == ("A-1", "A-3")
