"""`orgos eval answers`: the shell that computes the known answers over raw Mongo.

It runs the guards in the AC-43 order, reads Mongo through the reader login
only (find with a filter and a projection, AC-18), builds the corpus snapshot,
and hands it to the registered question functions. Every count happens in
Python over the snapshot; nothing here writes to Mongo.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pymongo.errors import PyMongoError

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.checker import effective_tables
from orgos.eval.describe import CORPUS_FILE, Corpus, read_corpus
from orgos.eval.entries import TimeEntry, choose_person_field, entry_authors, flag_bots
from orgos.eval.entries import rebuild_entries as rebuild
from orgos.eval.frozen import AMENDMENTS, QUESTIONS
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
from orgos.eval.models import Question, read_toml
from orgos.eval.snapshot import (
    ENTRY_ISSUES_FILTER,
    ENTRY_ISSUES_PROJECTION,
    Snapshot,
    build_snapshot,
    corpus_filter,
    corpus_projection,
)
from orgos.mongostore import describe as error_text
from orgos.mongostore.connect import reader_client, secrets
from orgos.mongostore.dump import DATABASE

KEY_AND_ACCOUNT_ID = ("key", "accountId")


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
