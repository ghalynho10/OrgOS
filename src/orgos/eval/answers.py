"""`orgos eval answers`: the shell that computes the known answers over raw Mongo.

It runs the guards in the AC-43 order, reads Mongo through the reader login
only (find with a filter and a projection, AC-18), builds the corpus snapshot,
and hands it to the registered question functions. Every count happens in
Python over the snapshot; nothing here writes to Mongo. A full run writes
`eval/answers.json` and the run record; `--check` and `--only` write nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymongo
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.checker import effective_tables
from orgos.eval.describe import CORPUS_FILE, Corpus, read_corpus
from orgos.eval.entries import TimeEntry, choose_person_field, entry_authors, flag_bots
from orgos.eval.entries import rebuild_entries as rebuild
from orgos.eval.frozen import AMENDMENTS, ANSWERS, QUESTIONS, git, sha256_file
from orgos.eval.guards import (
    SPIKE_RESULTS,
    Check,
    Hashes,
    Pushed,
    file_guards,
    read_targets,
    reconciliation_checks,
    require,
    uniqueness_checks,
)
from orgos.eval.known import REGISTRY, Registered, in_scope
from orgos.eval.models import Answer, PathAnswer, Question, read_toml
from orgos.eval.rules import other_person_share
from orgos.eval.snapshot import (
    ENTRY_ISSUES_FILTER,
    ENTRY_ISSUES_PROJECTION,
    Snapshot,
    build_snapshot,
    corpus_filter,
    corpus_projection,
)
from orgos.eval.writer import (
    answer_triggers,
    dumped,
    is_empty_answer,
    render,
    render_json,
    unfilled_triggers,
)
from orgos.mongostore import describe as error_text
from orgos.mongostore.connect import reader_client, secrets
from orgos.mongostore.dump import DATABASE

KEY_AND_ACCOUNT_ID = ("key", "accountId")
KNOWN_ANSWERS = Path("results/known_answers")
RUN_RECORD = KNOWN_ANSWERS / "run_record.json"
VERIFY_FILES = ("verify_before.txt", "verify_after.txt")
EVAL_CODE = "src/orgos/eval"


@dataclass(frozen=True)
class Prepared:
    """Everything the guards checked and the snapshot the answers are computed from."""

    hashes: Hashes
    pushed: Pushed
    questions: tuple[Question, ...]
    snapshot: Snapshot
    uniqueness: tuple[Check, ...]
    reconciliation: tuple[Check, ...]


def read_questions(root: Path) -> tuple[Question, ...]:
    """The questions with each amendment's `after` in place (AC-28)."""
    questions = read_toml(root / QUESTIONS).get("question", [])
    amended = (
        read_toml(root / AMENDMENTS).get("amendment", []) if (root / AMENDMENTS).is_file() else []
    )
    try:
        return tuple(Question.model_validate(t) for t in effective_tables(questions, amended))
    except ValidationError as error:
        raise EvalError(f"{QUESTIONS}: run `orgos eval check-questions`: {error}") from None


def cited_paths(questions: Sequence[Question]) -> frozenset[str]:
    """The union of every question's `fields`, the only paths the snapshot keeps."""
    return frozenset(path for question in questions for path in question.fields)


def collection_flags(
    documents: Iterator[Mapping[str, Any]],
) -> tuple[str, frozenset[str]]:
    """The person id field and the bot flags over every entry of the collection (AC-21)."""
    authors: list[Mapping[str, Any] | None] = []
    entries: dict[str, list[TimeEntry]] = {field: [] for field in KEY_AND_ACCOUNT_ID}
    for document in documents:
        authors.extend(entry_authors(document))
        for field in KEY_AND_ACCOUNT_ID:
            entries[field].extend(rebuild(document, field))
    person_field = choose_person_field(authors)
    return person_field, flag_bots(entries[person_field])


def read_snapshot(settings: Settings, corpus: Corpus, paths: frozenset[str]) -> Snapshot:
    """Two reader finds: every entry issue of the collection for the flags, then the corpus."""
    try:
        with reader_client(settings) as client:
            collection = client[DATABASE][corpus.collection]
            person_field, flagged = collection_flags(
                collection.find(ENTRY_ISSUES_FILTER, ENTRY_ISSUES_PROJECTION)
            )
            return build_snapshot(
                corpus.projects,
                person_field,
                flagged,
                collection.find(corpus_filter(corpus.projects), corpus_projection(paths)),
                paths,
            )
    except PyMongoError as error:
        raise EvalError(f"answers failed: {error_text(error, secrets(settings))}") from None


def prepare(settings: Settings, root: Path, *, check: bool) -> Prepared:
    """Run the guards up to reconciliation and build the snapshot; the first failure stops."""
    hashes, pushed = file_guards(root, check)
    questions = read_questions(root)
    snapshot = read_snapshot(settings, read_corpus(root / CORPUS_FILE), cited_paths(questions))
    uniqueness = uniqueness_checks(snapshot.issues)
    require("uniqueness", uniqueness)
    reconciliation = reconciliation_checks(snapshot, read_targets(root / SPIKE_RESULTS))
    require("reconciliation", reconciliation)
    return Prepared(hashes, pushed, questions, snapshot, uniqueness, reconciliation)


# The registry and the answers (AC-28, AC-32, AC-33)


@dataclass(frozen=True)
class Computed:
    """One question's answer and its amendment trigger lines."""

    question_id: str
    answer: Answer
    triggers: tuple[str, ...]


def check_registry(questions: Sequence[Question], registry: Mapping[str, Registered]) -> None:
    """Stop unless the registered ids and the question ids are the same set."""
    asked = {question.id for question in questions}
    missing, extra = sorted(asked - set(registry)), sorted(set(registry) - asked)
    if missing or extra:
        raise EvalError(f"registry: no function for {missing}; registered but not asked {extra}")


def check_sources(question_id: str, answer: Answer, spec: Registered) -> None:
    """Stop unless every source step lists exactly the registered kinds (AC-32).

    An empty step list is allowed for an empty answer or a "no" only.
    """
    step_lists = (
        [item.sources for item in answer.value]
        if isinstance(answer, PathAnswer)
        else [answer.sources]
    )
    for steps in step_lists:
        if not steps and is_empty_answer(answer):
            continue
        kinds = tuple(tuple(sorted(s)) for s in steps)
        expected = tuple(tuple(sorted(s)) for s in spec.steps)
        if kinds != expected:
            raise EvalError(f"{question_id}: source steps list {kinds}, registered {expected}")


def compute(
    prepared: Prepared, question_id: str, registry: Mapping[str, Registered] = REGISTRY
) -> Computed:
    """One question's answer from its registered function, with its triggers."""
    question = next((q for q in prepared.questions if q.id == question_id), None)
    if question is None:
        raise EvalError(f"{question_id} is not a question in {QUESTIONS}")
    spec = registry.get(question_id)
    if spec is None:
        raise EvalError(f"{question_id} has no registered function")
    answer = spec.function(prepared.snapshot, spec)
    if answer.shape != question.shape:
        raise EvalError(
            f"{question_id}: the function gives a {answer.shape}, not a {question.shape}"
        )
    check_sources(question_id, answer, spec)
    scope = in_scope(prepared.snapshot, spec)
    triggers = answer_triggers(question_id, answer, spec.cut)
    return Computed(
        question_id, answer, triggers + unfilled_triggers(question_id, question.fields, scope)
    )


def compute_all(
    prepared: Prepared, registry: Mapping[str, Registered] = REGISTRY
) -> tuple[Computed, ...]:
    """Every answer, after the registry check (full runs only, AC-43)."""
    check_registry(prepared.questions, registry)
    return tuple(
        compute(prepared, q.id, registry) for q in sorted(prepared.questions, key=lambda q: q.id)
    )


def first_difference(computed: bytes, written: bytes) -> str | None:
    """The first question whose answer differs between two `answers.json` texts, or None."""
    if computed == written:
        return None
    try:
        old = json.loads(written).get("answers")
    except (ValueError, AttributeError):
        return f"{ANSWERS} as a whole (not an answers file)"
    new = json.loads(computed)["answers"]
    old = old if isinstance(old, dict) else {}
    for question_id in sorted(set(new) | set(old)):
        if new.get(question_id) != old.get(question_id):
            return str(question_id)
    return "the file header or its formatting"


# The run record (AC-35, AC-36)


@dataclass(frozen=True)
class RunContext:
    """What the run record states about where and with what it ran."""

    time: str
    commit: str
    dirty: bool
    python: str
    pymongo: str


def run_context(root: Path) -> RunContext:
    """The time, the git commit, uncommitted changes under the eval code, and versions."""
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain", "--", EVAL_CODE)
    if head.returncode != 0 or status.returncode != 0:
        raise EvalError(f"run record: git failed: {(head.stderr or status.stderr).strip()}")
    return RunContext(
        time=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        commit=head.stdout.strip(),
        dirty=bool(status.stdout.strip()),
        python=platform.python_version(),
        pymongo=pymongo.version,
    )


def run_record(prepared: Prepared, context: RunContext, root: Path) -> dict[str, Any]:
    """The run record (AC-35): no connection string, user name or password."""
    num, den = other_person_share(prepared.snapshot.entries, prepared.snapshot.flagged)
    return {
        "time": context.time,
        "git": {"commit": context.commit, "dirty": context.dirty},
        "sha256": {
            "questions": prepared.hashes.questions,
            "amendments": prepared.hashes.amendments,
            "answers": sha256_file(root / ANSWERS),
        },
        "versions": {"python": context.python, "pymongo": context.pymongo},
        "person_id_field": prepared.snapshot.person_field,
        "flagged_accounts": sorted(prepared.snapshot.flagged),
        "pushed_checks": prepared.pushed.record(),
        "uniqueness": {check.name: check.record() for check in prepared.uniqueness},
        "reconciliation": {check.name: check.record() for check in prepared.reconciliation},
        "verify_sha256": {
            name: sha256_file(root / KNOWN_ANSWERS / name)
            for name in VERIFY_FILES
            if (root / KNOWN_ANSWERS / name).is_file()
        },
        "other_person_share": {"num": num, "den": den},
    }


# The three modes


def notes(prepared: Prepared) -> tuple[str, ...]:
    """A line per uniqueness check that is a data quality note and did not hold (AC-26)."""
    return tuple(
        f"uniqueness note: {check.name}: {len(check.found)} found"
        for check in prepared.uniqueness
        if not check.stops and not check.ok
    )


def only(prepared: Prepared, question_id: str) -> tuple[str, ...]:
    """`--only`: one answer printed with its triggers; nothing is written (AC-45)."""
    computed = compute(prepared, question_id)
    shown = render_json({question_id: dumped(computed.answer)}).decode().rstrip("\n")
    return (*notes(prepared), shown, *(computed.triggers or (f"{question_id}: no triggers",)))


def check_written(prepared: Prepared, root: Path) -> tuple[str, ...]:
    """`--check`: recompute in memory and require identical bytes; nothing is written (AC-34)."""
    computed = compute_all(prepared)
    rendered = render(
        {c.question_id: c.answer for c in computed},
        prepared.hashes.questions,
        prepared.hashes.amendments,
    )
    path = root / ANSWERS
    if not path.is_file():
        raise EvalError(f"check: {ANSWERS} not found")
    differs = first_difference(rendered, path.read_bytes())
    if differs is not None:
        raise EvalError(f"check: {differs} differs from {ANSWERS}")
    return (*notes(prepared), f"check: {len(computed)} answers match {ANSWERS} byte for byte")


def write_all(prepared: Prepared, root: Path) -> tuple[str, ...]:
    """A full run: write `answers.json`, then the run record, and print every trigger."""
    computed = compute_all(prepared)
    rendered = render(
        {c.question_id: c.answer for c in computed},
        prepared.hashes.questions,
        prepared.hashes.amendments,
    )
    (root / ANSWERS).write_bytes(rendered)
    record = run_record(prepared, run_context(root), root)
    (root / RUN_RECORD).parent.mkdir(parents=True, exist_ok=True)
    (root / RUN_RECORD).write_bytes(render_json(record))
    triggers = tuple(line for c in computed for line in c.triggers)
    return (
        *notes(prepared),
        *triggers,
        f"wrote {ANSWERS} ({len(computed)} answers, {len(triggers)} triggers) and {RUN_RECORD}",
    )


def answers(settings: Settings, root: Path, *, check: bool, only_id: str | None) -> tuple[str, ...]:
    """`orgos eval answers`: the guards in the AC-43 order, then one of the three modes."""
    if check and only_id is not None:
        raise EvalError("use --check or --only, not both")
    prepared = prepare(settings, root, check=check)
    if only_id is not None:
        return only(prepared, only_id)
    return check_written(prepared, root) if check else write_all(prepared, root)
