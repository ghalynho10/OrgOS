"""dlt ingest from the MongoDB staging store into Postgres schema `raw` (spec 0009).

This module is the pure core: the corpus project keys and the check that turns
one Mongo document into one `raw.jira_issue` row. The Mongo read and the dlt
load live in `orgos.ingest.load`. Every failure the ingest can expect is an
`IngestError`, which the CLI turns into exit code 5.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bson import ObjectId


class IngestError(Exception):
    """Raised when the Mongo read, the raw shape, the dlt load or the raw count fails."""


# The 7 locked corpus projects (spec 0004). A unit test fails if these differ
# from the `group` of spikes/0001-time-data-coverage/results/corpus.json.
CORPUS_PROJECTS: tuple[str, ...] = ("BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER")

# Every corpus document has exactly these top level keys (spec 0009 AC-4).
DOCUMENT_KEYS = frozenset({"_id", "key", "id", "self", "expand", "fields", "changelog"})


def to_raw_row(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `raw.jira_issue` row for one Mongo document, `_id` as 24 character hex text.

    A missing or extra top level key, or an `_id` that is not an ObjectId,
    raises `IngestError` naming the document.
    """
    doc_id = doc.get("_id")
    name = str(doc_id) if doc_id is not None else "(no _id)"
    missing = sorted(DOCUMENT_KEYS - doc.keys())
    extra = sorted(doc.keys() - DOCUMENT_KEYS)
    if missing or extra:
        problems = []
        if missing:
            problems.append(f"missing {', '.join(missing)}")
        if extra:
            problems.append(f"extra {', '.join(extra)}")
        raise IngestError(
            f"Document {name} has an unexpected shape: {'; '.join(problems)} "
            "(spec 0009 AC-4 expects exactly the 7 top level keys)"
        )
    if not isinstance(doc_id, ObjectId):
        raise IngestError(f"Document {name} has an _id that is not an ObjectId")
    return {
        "mongo_id": str(doc_id),
        "key": doc["key"],
        "id": doc["id"],
        "self": doc["self"],
        "expand": doc["expand"],
        "fields": doc["fields"],
        "changelog": doc["changelog"],
    }


def check_raw_count(mongo_count: int, rows: int) -> None:
    """Raise `IngestError` unless `raw.jira_issue` holds every document Mongo counted (AC-5)."""
    if mongo_count != rows:
        raise IngestError(
            f"raw.jira_issue holds {rows:,} rows but Mongo counted {mongo_count:,} documents"
        )
