"""The guards `orgos eval answers` runs before any answer, in the AC-43 order.

The frozen refusal (AC-6), the hashes (AC-19), the pushed checks (AC-4,
AC-41), uniqueness (AC-26) and reconciliation (AC-27, AC-21). Each stops the
run with an `EvalError` naming it; the registry check (AC-28) lives with the
registry. The first three read files and git only; the last two are pure
functions over the snapshot and the spike's results, and return one check
object per rule for the run record (AC-35).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orgos.eval import EvalError
from orgos.eval.frozen import AMENDMENTS, ANSWERS, FROZEN, QUESTIONS, git, parse_frozen, sha256_file
from orgos.eval.rules import logged_seconds, people_who_logged
from orgos.eval.snapshot import Issue, Snapshot

SPIKE_RESULTS = Path("spikes/0001-time-data-coverage/results")
COLLECTION = "Jira"


@dataclass(frozen=True)
class Check:
    """One uniqueness or reconciliation rule: what was expected, what was found, whether it held.

    `stops` is False for a rule that is a data quality note only.
    """

    name: str
    expected: Any
    found: Any
    ok: bool
    stops: bool = True

    def record(self) -> dict[str, Any]:
        """The run record object for this check."""
        return {"expected": self.expected, "found": self.found, "ok": self.ok}


@dataclass(frozen=True)
class Pushed:
    """The commits the pushed checks looked at and the remote branches holding each."""

    step1_commit: str
    step1_branches: tuple[str, ...]
    amendment_commit: str | None
    amendment_branches: tuple[str, ...]

    def record(self) -> dict[str, Any]:
        """The run record object for the pushed checks."""
        return {
            "step1_commit": self.step1_commit,
            "step1_branches": list(self.step1_branches),
            "amendment_commit": self.amendment_commit,
            "amendment_branches": list(self.amendment_branches),
        }


@dataclass(frozen=True)
class Hashes:
    """The sha256 of the question file and of the amendments file when it exists."""

    questions: str
    amendments: str | None


# 1. The frozen refusal (AC-6)


def _pins(root: Path) -> dict[str, str]:
    path = root / FROZEN
    if not path.is_file():
        raise EvalError(f"hashes: {FROZEN} not found")
    pins, problems = parse_frozen(path.read_text(encoding="utf-8"))
    if problems:
        raise EvalError(f"hashes: {problems[0]}")
    return {pinned: sha for sha, pinned in pins}


def refuse_if_frozen(root: Path) -> None:
    """Stop when `FROZEN` lists `answers.json`: the set is frozen and nothing is written."""
    if (root / FROZEN).is_file() and ANSWERS in _pins(root):
        raise EvalError(f"frozen refusal: {FROZEN} lists {ANSWERS}; the set is frozen")


# 2. The hashes (AC-19)


def check_hashes(root: Path) -> Hashes:
    """Stop unless `questions.toml`, and `amendments.toml` when it exists, match `FROZEN`."""
    pins = _pins(root)

    def pinned(path: str) -> str:
        digest = sha256_file(root / path)
        if pins.get(path) != digest:
            raise EvalError(f"hashes: {path} does not match its line in {FROZEN}")
        return digest

    if not (root / QUESTIONS).is_file():
        raise EvalError(f"hashes: {QUESTIONS} not found")
    questions = pinned(QUESTIONS)
    return Hashes(questions, pinned(AMENDMENTS) if (root / AMENDMENTS).is_file() else None)


# 3. The pushed checks (AC-4, AC-41)


def _git_lines(root: Path, *args: str) -> tuple[str, ...]:
    done = git(root, *args)
    if done.returncode != 0:
        raise EvalError(f"pushed checks: git {args[0]} failed: {done.stderr.strip()}")
    return tuple(line.strip() for line in done.stdout.splitlines() if line.strip())


def _remote_branches(root: Path, commit: str) -> tuple[str, ...]:
    return tuple(
        sorted(b for b in _git_lines(root, "branch", "-r", "--contains", commit) if "->" not in b)
    )


def check_pushed(root: Path) -> Pushed:
    """Stop unless the step 1 commit and the last amendment commit are on a remote branch.

    The step 1 commit is the one that first added `questions.toml`. The pinned
    files must also match their committed copies, or what was pushed is not
    what would be answered.
    """
    dirty = _git_lines(root, "status", "--porcelain", "--", QUESTIONS, AMENDMENTS, FROZEN)
    if dirty:
        raise EvalError(f"pushed checks: uncommitted changes to {', '.join(d[3:] for d in dirty)}")
    added = _git_lines(root, "log", "--diff-filter=A", "--format=%H", "--", QUESTIONS)
    if not added:
        raise EvalError(f"pushed checks: no commit adds {QUESTIONS}")
    step1 = added[-1]
    step1_branches = _remote_branches(root, step1)
    if not step1_branches:
        raise EvalError(f"pushed checks: the step 1 commit {step1[:12]} is on no remote branch")
    amendment: str | None = None
    amendment_branches: tuple[str, ...] = ()
    if (root / AMENDMENTS).is_file():
        amendment = _git_lines(root, "log", "-1", "--format=%H", "--", AMENDMENTS)[0]
        amendment_branches = _remote_branches(root, amendment)
        if not amendment_branches:
            raise EvalError(
                f"pushed checks: the amendment commit {amendment[:12]} is on no remote branch"
            )
    return Pushed(step1, step1_branches, amendment, amendment_branches)


def file_guards(root: Path, check: bool) -> tuple[Hashes, Pushed]:
    """The first three guards in the AC-43 order; `--check` skips the frozen refusal."""
    if not check:
        refuse_if_frozen(root)
    hashes = check_hashes(root)
    return hashes, check_pushed(root)


# 4. Uniqueness (AC-26)


def _repeated(values: Iterable[str]) -> list[str]:
    return sorted(value for value, n in Counter(values).items() if n > 1)


def _many(pairs: Iterable[tuple[str, object]]) -> list[str]:
    seen: dict[str, set[object]] = {}
    for key, value in pairs:
        seen.setdefault(key, set()).add(value)
    return sorted(key for key, values in seen.items() if len(values) > 1)


def uniqueness_checks(issues: Sequence[Issue]) -> tuple[Check, ...]:
    """The five AC-26 checks over the collapsed corpus; the fifth is a note and never stops."""
    found = (
        ("issue key on one document", _repeated(i.key for i in issues), True),
        ("project key to one project", _many((i.project_key, i.project_id) for i in issues), True),
        ("changelog history id once", _repeated(h for i in issues for h in i.history_ids), True),
        (
            "issue link id to one pair",
            _many(
                (link.link_id, (link.inward_key, link.outward_key))
                for i in issues
                for link in i.links
            ),
            True,
        ),
        ("person key to one name", _many(pair for i in issues for pair in i.authors), False),
    )
    return tuple(Check(name, [], dupes, not dupes, stops) for name, dupes, stops in found)


def require(guard: str, checks: Iterable[Check]) -> None:
    """Stop at the first failed check that stops, naming the guard, the rule and what was found."""
    for check in checks:
        if check.stops and not check.ok:
            raise EvalError(f"{guard}: {check.name} failed: found {check.found}")


# 5. Reconciliation (AC-27, AC-21)


@dataclass(frozen=True)
class Targets:
    """What the spike recorded, to reconcile with: per project seconds and people, and more."""

    positive_seconds: Mapping[str, int]
    people: Mapping[str, int]
    mismatches: tuple[str, ...]
    flagged: tuple[str, ...]
    person_field: str


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise EvalError(f"reconciliation: {path} cannot be read: {error.strerror}") from None


def read_targets(results: Path = SPIKE_RESULTS) -> Targets:
    """The spike's numbers for the Jira collection and its corpus projects."""
    try:
        return _targets(results)
    except (ValueError, KeyError, TypeError) as error:
        raise EvalError(
            f"reconciliation: the spike results in {results} are malformed: {error!r}"
        ) from None


def _targets(results: Path) -> Targets:
    corpus = json.loads(_read(results / "corpus.json"))
    projects = {p["key"]: p for p in corpus["projects"] if p["key"] in corpus["group"]}
    flagged = csv.DictReader(_read(results / "flagged_accounts.csv").splitlines())
    mismatches = csv.DictReader(_read(results / "entry_mismatches.csv").splitlines())
    run = json.loads(_read(results / "run_record.json"))["run"]
    return Targets(
        positive_seconds={key: int(p["positive_seconds"]) for key, p in projects.items()},
        people={key: int(p["people"]) for key, p in projects.items()},
        mismatches=tuple(
            sorted(
                row["issue_key"]
                for row in mismatches
                if row["collection"] == COLLECTION and row["project"] in projects
            )
        ),
        flagged=tuple(sorted(row["account"] for row in flagged if row["collection"] == COLLECTION)),
        person_field=run["person_id_field"][COLLECTION],
    )


def entry_mismatches(snapshot: Snapshot) -> tuple[str, ...]:
    """Corpus issues whose net entry sum differs from `fields.timespent` (AC-27).

    Over issues with no flagged entry that have at least one entry or a
    `timespent` above 0, null read as 0.
    """
    by_issue: dict[str, list[int]] = {}
    with_flag: set[str] = set()
    for entry in snapshot.entries:
        by_issue.setdefault(entry.issue_key, []).append(entry.amount)
        if entry.author_key is not None and entry.author_key in snapshot.flagged:
            with_flag.add(entry.issue_key)
    return tuple(
        sorted(
            issue.key
            for issue in snapshot.issues
            if issue.key not in with_flag
            and (issue.key in by_issue or (issue.timespent or 0) > 0)
            and sum(by_issue.get(issue.key, ())) != (issue.timespent or 0)
        )
    )


def reconciliation_checks(snapshot: Snapshot, targets: Targets) -> tuple[Check, ...]:
    """The AC-21 and AC-27 checks against the spike, in a fixed order."""
    entries_of: dict[str, list[Any]] = {project: [] for project in snapshot.projects}
    for entry in snapshot.entries:
        if entry.project_key in entries_of:
            entries_of[entry.project_key].append(entry)
    seconds = {p: logged_seconds(entries_of[p], snapshot.flagged) for p in snapshot.projects}
    people = {p: len(people_who_logged(entries_of[p], snapshot.flagged)) for p in snapshot.projects}
    found = (
        ("person id field", targets.person_field, snapshot.person_field),
        ("flagged accounts", list(targets.flagged), sorted(snapshot.flagged)),
        ("positive seconds per project", dict(targets.positive_seconds), seconds),
        ("people per project", dict(targets.people), people),
        ("entry mismatches", list(targets.mismatches), list(entry_mismatches(snapshot))),
    )
    return tuple(Check(name, expected, got, expected == got) for name, expected, got in found)
