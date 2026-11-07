"""The ingest shell: read the corpus from Mongo once and load it whole into `raw` with dlt.

`read_corpus` counts and then reads the 7 corpus projects through the reader
login (one filtered pass, never by key prefix, never a write). `load_raw` runs
the dlt pipeline `orgos_raw` into `raw.jira_issue` as a truncate and insert
replace, with `fields` and `changelog` kept whole as jsonb and no child tables
(spec 0009 AC-2 to AC-6). `raw_count` reads the row count back for AC-5.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import dlt
import psycopg
from dlt.common.schema.typing import TTableSchemaColumns
from dlt.pipeline.exceptions import PipelineStepFailed
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from orgos.ingest import IngestError
from orgos.mongostore import describe
from orgos.mongostore.dump import DATABASE

log = logging.getLogger(__name__)

COLLECTION = "Jira"
BATCH_SIZE = 500

PIPELINE_NAME = "orgos_raw"
DATASET = "raw"
TABLE = "jira_issue"
REPLACE_STRATEGY = "truncate-and-insert"
# All 7 columns hinted, so a batch with only nulls in one column cannot change
# its type; the two json columns stay whole, keeping keys such as `fromString`.
COLUMNS: TTableSchemaColumns = {
    "mongo_id": {"data_type": "text", "nullable": False},
    "key": {"data_type": "text"},
    "id": {"data_type": "text"},
    "self": {"data_type": "text"},
    "expand": {"data_type": "text"},
    "fields": {"data_type": "json"},
    "changelog": {"data_type": "json"},
}


@dataclass(frozen=True)
class CorpusRead:
    """The count Mongo reported for the corpus filter, and the documents, read lazily once."""

    count: int
    documents: Iterator[Mapping[str, Any]]


def corpus_filter(projects: Iterable[str]) -> dict[str, Any]:
    """Corpus documents by current `fields.project.key`, never by key prefix."""
    return {"fields.project.key": {"$in": list(projects)}}


def read_corpus(client: MongoClient[dict[str, Any]], projects: Iterable[str]) -> CorpusRead:
    """Count the corpus documents, then read them in one pass of batches of 500."""
    collection = client[DATABASE][COLLECTION]
    query = corpus_filter(projects)
    try:
        count = collection.count_documents(query)
    except PyMongoError as error:
        raise IngestError(f"Mongo count failed: {describe(error, ())}") from None
    log.info("Mongo reports %s corpus documents", f"{count:,}")
    return CorpusRead(count=count, documents=_documents(collection, query))


def _documents(collection: Any, query: dict[str, Any]) -> Iterator[Mapping[str, Any]]:
    try:
        yield from collection.find(query, batch_size=BATCH_SIZE)
    except PyMongoError as error:
        raise IngestError(f"Mongo read failed: {describe(error, ())}") from None


def _ingest_cause(error: BaseException) -> IngestError | None:
    # dlt wraps an error raised inside the row generator several times.
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, IngestError):
            return current
        seen.add(id(current))
        inner = getattr(current, "exception", None)
        current = inner if isinstance(inner, BaseException) else current.__cause__
    return None


def _redact(message: str, url: str) -> str:
    password = urlsplit(url).password
    if not password:
        return message
    for value in (password, unquote(password)):
        message = message.replace(value, "***")
    return message


def load_raw(url: str, rows: Iterable[Mapping[str, Any]], pipelines_dir: Path) -> str:
    """Replace `raw.jira_issue` with `rows` through dlt; return the dlt load ID.

    Pending packages from an earlier failed run are dropped first, so a bad
    package cannot block this one. Any failure raises `IngestError`, and an
    `IngestError` raised while reading the rows comes back unwrapped.
    """
    dlt.config["runtime.dlthub_telemetry"] = False
    pipeline = dlt.pipeline(
        pipeline_name=PIPELINE_NAME,
        pipelines_dir=str(pipelines_dir),
        destination=dlt.destinations.postgres(credentials=url, replace_strategy=REPLACE_STRATEGY),
        dataset_name=DATASET,
    )
    pipeline.abort_packages()
    resource = dlt.resource(
        iter(rows),
        name=TABLE,
        write_disposition="replace",
        max_table_nesting=0,
        columns=COLUMNS,
    )
    try:
        info = pipeline.run(resource)
    except PipelineStepFailed as error:
        cause = _ingest_cause(error)
        if cause is not None:
            raise cause from None
        raise IngestError(
            _redact(f"dlt {error.step} step failed: {error.exception}", url)
        ) from None
    load_id = info.loads_ids[-1]
    log.info("dlt load %s complete", load_id)
    return load_id


def raw_count(conninfo: str) -> int:
    """Rows in `raw.jira_issue`, read as the owner."""
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(f"SELECT count(*) FROM {DATASET}.{TABLE}").fetchone()
    return int(row[0]) if row else 0
