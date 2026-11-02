"""The known answers' Mongo reads on a real Mongo holding fixture issues (spec 0005, AC-18, AC-21).

Three fixture projects are loaded and two of them are the corpus, so the
whole collection pass (bot flags, person field) and the corpus pass are told
apart. The reads go through a reader login made here and leave Mongo as it was.
"""

import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from bson import json_util
from pymongo import MongoClient
from testcontainers.community.mongodb import MongoDbContainer

from orgos.config import Settings, load_settings
from orgos.eval import EvalError
from orgos.eval.answers import read_snapshot
from orgos.eval.describe import Corpus
from orgos.eval.entries import flag_bots, rebuild_entries
from orgos.eval.snapshot import Collapsed
from orgos.mongostore.dump import DATABASE
from tests.integration.conftest import MONGO_ROOT_PASSWORD, MONGO_ROOT_USER

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
LOADED = ("BAM", "CLOV", "CWD")
CORPUS = Corpus("Jira", ("CLOV", "CWD"))
READER_USER = "orgos_answers_reader"
PATHS = frozenset(
    {
        "fields.created",
        "fields.assignee.key",
        "fields.resolution.name",
        "fields.components[].name",
        "changelog.items[field=status].fromString/toString",
    }
)


def _settings(mongo: MongoDbContainer, reader_password: str) -> Settings:
    return load_settings(
        _env_file=None,
        ORGOS_MONGO_HOST=mongo.get_container_host_ip(),
        ORGOS_MONGO_PORT=int(mongo.get_exposed_port(27017)),
        MONGO_INITDB_ROOT_USERNAME=MONGO_ROOT_USER,
        MONGO_INITDB_ROOT_PASSWORD=MONGO_ROOT_PASSWORD,
        ORGOS_MONGO_READER_USER=READER_USER,
        ORGOS_MONGO_READER_PASSWORD=reader_password,
        POSTGRES_PASSWORD="unused",
        ORGOS_PG_OWNER_PASSWORD="unused",
        ORGOS_PG_READER_PASSWORD="unused",
        NEO4J_AUTH="neo4j/unused-password",
    )


def _docs(project: str) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = json_util.loads((FIXTURES / f"{project}.json").read_text())
    return docs


@pytest.fixture(scope="module")
def reader_password() -> str:
    return secrets.token_hex(8)


@pytest.fixture(scope="module")
def loaded(mongo: MongoDbContainer, reader_password: str) -> Iterator[MongoClient[dict[str, Any]]]:
    settings = _settings(mongo, reader_password)
    with MongoClient(settings.mongo_uri, tz_aware=True) as root:
        for project in LOADED:
            root[DATABASE].Jira.insert_many(_docs(project))
        root.admin.command(
            "createUser", READER_USER, pwd=reader_password, roles=[{"role": "read", "db": DATABASE}]
        )
        try:
            yield root
        finally:
            root.admin.command("dropUser", READER_USER)
            root.drop_database(DATABASE)


def test_the_snapshot_reads_the_corpus_and_flags_over_the_whole_collection(
    mongo: MongoDbContainer, loaded: MongoClient[dict[str, Any]], reader_password: str
) -> None:
    before = loaded[DATABASE].Jira.count_documents({})
    snapshot = read_snapshot(_settings(mongo, reader_password), CORPUS, PATHS)
    assert loaded[DATABASE].Jira.count_documents({}) == before

    corpus_docs = [doc for project in CORPUS.projects for doc in _docs(project)]
    assert sorted(issue.key for issue in snapshot.issues) == sorted(d["key"] for d in corpus_docs)
    expected = {e for d in corpus_docs for e in rebuild_entries(d, "key")}
    assert set(snapshot.entries) == expected
    all_entries = [
        e for project in LOADED for d in _docs(project) for e in rebuild_entries(d, "key")
    ]
    assert snapshot.person_field == "key"
    assert snapshot.flagged == flag_bots(all_entries)

    by_key = {d["key"]: d for d in corpus_docs}
    for issue in snapshot.issues:
        fields = by_key[issue.key]["fields"]
        assert issue.values("fields.created") == (fields["created"],)
        assignee = (fields.get("assignee") or {}).get("key")
        assert issue.values("fields.assignee.key") == ((assignee,) if assignee else ())
        assert issue.timespent == fields.get("timespent")
    assert all(link.inward_key in by_key and link.outward_key in by_key for link in snapshot.links)


def test_copies_on_the_real_store_collapse_or_stop(
    mongo: MongoDbContainer, loaded: MongoClient[dict[str, Any]], reader_password: str
) -> None:
    jira = loaded[DATABASE].Jira
    settings = _settings(mongo, reader_password)
    original = jira.find_one({"fields.project.key": "CWD"})
    assert original is not None
    copy = {name: value for name, value in original.items() if name != "_id"}
    copy_id = jira.insert_one(copy).inserted_id
    try:
        before = read_snapshot(settings, CORPUS, PATHS)
        low, high = sorted((original["_id"], copy_id))
        assert before.collapsed == (Collapsed(original["key"], str(low), (str(high),)),)
        assert [i.key for i in before.issues].count(original["key"]) == 1
        jira.update_one({"_id": copy_id}, {"$set": {"fields.votes.votes": -1}})
        with pytest.raises(EvalError, match=f"uniqueness: {original['key']} is on 2 documents"):
            read_snapshot(settings, CORPUS, PATHS)
    finally:
        jira.delete_one({"_id": copy_id})


def test_a_failed_login_is_an_eval_error_without_the_password(
    mongo: MongoDbContainer, loaded: MongoClient[dict[str, Any]]
) -> None:
    wrong = secrets.token_hex(8)
    with pytest.raises(EvalError, match="answers failed") as error:
        read_snapshot(_settings(mongo, wrong), CORPUS, PATHS)
    assert wrong not in str(error.value)
