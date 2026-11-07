"""The pure ingest core and the config the SQL load adds (spec 0009 AC-2 to AC-5, AC-14, AC-21)."""

import json
import secrets
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from bson import ObjectId

from orgos.config import DBT_TIMEZONE, Settings, dbt_subprocess_env, load_settings
from orgos.ingest import CORPUS_PROJECTS, DOCUMENT_KEYS, IngestError, check_raw_count, to_raw_row
from orgos.ingest.load import corpus_filter

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads(
    (ROOT / "spikes/0001-time-data-coverage/results/corpus.json").read_text(encoding="utf-8")
)


def _doc(**changes: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "_id": ObjectId("61d5bb3df4d395ee2217f3b5"),
        "key": "BAM-1",
        "id": "1",
        "self": "https://example.invalid/1",
        "expand": "changelog",
        "fields": {"project": {"key": "BAM"}},
        "changelog": {"histories": []},
    }
    doc.update(changes)
    return doc


# covers: AC-2 (the 7 keys are the corpus.json projects)
def test_corpus_projects_match_corpus_json() -> None:
    assert sorted(CORPUS_PROJECTS) == sorted(CORPUS["group"])
    assert sorted(CORPUS_PROJECTS) == sorted(project["key"] for project in CORPUS["projects"])


# covers: AC-2 (filter by current project key, never by key prefix)
def test_corpus_filter_selects_by_project_key() -> None:
    assert corpus_filter(CORPUS_PROJECTS) == {"fields.project.key": {"$in": list(CORPUS_PROJECTS)}}


# covers: AC-3 (one row per document, _id as 24 character hex)
def test_to_raw_row_keeps_every_key_and_converts_the_id() -> None:
    row = to_raw_row(_doc())
    assert row == {
        "mongo_id": "61d5bb3df4d395ee2217f3b5",
        "key": "BAM-1",
        "id": "1",
        "self": "https://example.invalid/1",
        "expand": "changelog",
        "fields": {"project": {"key": "BAM"}},
        "changelog": {"histories": []},
    }
    assert len(row["mongo_id"]) == 24


# covers: AC-4 (a missing top level key stops the load and names the document)
@pytest.mark.parametrize("missing", sorted(DOCUMENT_KEYS - {"_id"}))
def test_to_raw_row_rejects_a_missing_key(missing: str) -> None:
    doc = _doc()
    del doc[missing]
    with pytest.raises(IngestError, match=rf"61d5bb3df4d395ee2217f3b5 .*missing {missing}"):
        to_raw_row(doc)


# covers: AC-4 (an eighth top level key stops the load and names the document)
def test_to_raw_row_rejects_an_extra_key() -> None:
    with pytest.raises(IngestError, match=r"61d5bb3df4d395ee2217f3b5 .*extra renderedFields"):
        to_raw_row(_doc(renderedFields={}))


def test_to_raw_row_names_both_problems_together() -> None:
    doc = _doc(names={})
    del doc["expand"]
    with pytest.raises(IngestError, match=r"missing expand; extra names"):
        to_raw_row(doc)


def test_to_raw_row_rejects_a_document_with_no_id() -> None:
    doc = _doc()
    del doc["_id"]
    with pytest.raises(IngestError, match=r"\(no _id\).*missing _id"):
        to_raw_row(doc)


def test_to_raw_row_rejects_an_id_that_is_not_an_object_id() -> None:
    with pytest.raises(IngestError, match="BAM-1-copy has an _id that is not an ObjectId"):
        to_raw_row(_doc(_id="BAM-1-copy"))


# covers: AC-5 (a raw count that differs from Mongo stops the load)
def test_check_raw_count_passes_when_equal() -> None:
    check_raw_count(87_839, 87_839)


def test_check_raw_count_names_both_counts() -> None:
    with pytest.raises(IngestError, match="holds 87,838 rows but Mongo counted 87,839"):
        check_raw_count(87_839, 87_838)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    owner_password = f"{secrets.token_hex(8)}@:/ %+"
    return load_settings(
        _env_file=None,
        POSTGRES_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_OWNER_PASSWORD=owner_password,
        ORGOS_PG_READER_PASSWORD=secrets.token_hex(8),
        MONGO_INITDB_ROOT_PASSWORD=secrets.token_hex(8),
        ORGOS_MONGO_READER_PASSWORD=secrets.token_hex(8),
        NEO4J_AUTH=f"neo4j/{secrets.token_hex(8)}",
        ORGOS_PG_PORT=55432,
    )


# covers: AC-21 (Settings builds the owner URL dlt receives, the password quoted)
def test_owner_url_quotes_the_password(settings: Settings) -> None:
    parts = urlsplit(settings.pg_owner_url)
    assert parts.scheme == "postgresql"
    assert parts.hostname == "127.0.0.1"
    assert parts.port == 55432
    assert parts.path == "/orgos"
    assert unquote(parts.username or "") == "orgos_owner"
    assert unquote(parts.password or "") == settings.pg_owner_password.get_secret_value()


# covers: AC-14, AC-21 (PGTZ set last, over an inherited value)
def test_dbt_subprocess_env_sets_pgtz_last(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGTZ", "UTC")
    monkeypatch.setenv("ORGOS_UNRELATED", "kept")
    env = dbt_subprocess_env(settings)
    assert env["PGTZ"] == DBT_TIMEZONE == "America/St_Johns"
    assert env["ORGOS_UNRELATED"] == "kept"
    assert env["ORGOS_PG_PORT"] == "55432"
    assert env["ORGOS_PG_OWNER_PASSWORD"] == settings.pg_owner_password.get_secret_value()
