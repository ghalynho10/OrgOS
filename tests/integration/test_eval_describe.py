"""`orgos eval describe` on a real Mongo holding fixture issues (spec 0005, AC-13, AC-14, AC-18).

The issues come from `tests/fixtures/corpus/`. Describe logs in as a reader
made here, writes into a temporary folder, and must leave Mongo as it was.
"""

import json
import re
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
from orgos.eval.checker import read_field_lists
from orgos.eval.describe import DESCRIPTION_NAME, LOG_NAME, describe
from orgos.eval.fields import ALWAYS_USABLE
from orgos.mongostore.dump import DATABASE
from tests.integration.conftest import MONGO_ROOT_PASSWORD, MONGO_ROOT_USER

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "corpus"
PROJECTS = ("CLOV", "CWD")
READER_USER = "orgos_eval_reader"
CATEGORIES = [
    "shape",
    "fill_rate",
    "distinct_values",
    "exception:links_parents",
    "exception:negative_entries",
]
BLOCK = re.compile(r"## (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) · (\S+)\n\n```\n(.+)\n```\n\n")


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


@pytest.fixture(scope="module")
def reader_password() -> str:
    return secrets.token_hex(8)


@pytest.fixture(scope="module")
def loaded(mongo: MongoDbContainer, reader_password: str) -> Iterator[MongoClient[dict[str, Any]]]:
    settings = _settings(mongo, reader_password)
    with MongoClient(settings.mongo_uri, tz_aware=True) as root:
        for project in PROJECTS:
            docs = json_util.loads((FIXTURES / f"{project}.json").read_text())
            root[DATABASE].Jira.insert_many(docs)
        root.admin.command(
            "createUser", READER_USER, pwd=reader_password, roles=[{"role": "read", "db": DATABASE}]
        )
        try:
            yield root
        finally:
            root.admin.command("dropUser", READER_USER)
            root.drop_database(DATABASE)


@pytest.fixture
def corpus_file(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({"collection": "Jira", "group": list(PROJECTS)}))
    return path


def _state(root: MongoClient[dict[str, Any]]) -> tuple[Any, ...]:
    db = root[DATABASE]
    return (
        sorted(db.list_collection_names()),
        db.Jira.count_documents({}),
        sorted(index["name"] for index in db.Jira.list_indexes()),
    )


def test_describe_logs_one_block_per_query_and_changes_nothing(
    mongo: MongoDbContainer,
    loaded: MongoClient[dict[str, Any]],
    reader_password: str,
    corpus_file: Path,
    tmp_path: Path,
) -> None:
    settings = _settings(mongo, reader_password)
    before = _state(loaded)
    out = tmp_path / "eval"

    result = describe(settings, out, corpus_file)

    assert _state(loaded) == before
    log = (out / LOG_NAME).read_text()
    blocks = BLOCK.findall(log)
    assert [category for _, category, _ in blocks] == CATEGORIES
    assert "".join(m.group(0) for m in BLOCK.finditer(log)) == log
    for secret in (reader_password, MONGO_ROOT_PASSWORD, READER_USER, "mongodb://"):
        assert secret not in log
    assert re.search(r"\b(CLOV|CWD)-\d", log) is None  # no issue key, so no output

    text = (out / DESCRIPTION_NAME).read_text()
    lists = read_field_lists(text)
    assert set(ALWAYS_USABLE) <= lists.usable
    assert {"key", "fields.project.key"} <= lists.usable
    assert "fields.description" in lists.free_text
    assert not lists.usable & lists.free_text
    assert (result.usable, result.free_text) == (len(lists.usable), len(lists.free_text))

    describe(settings, out, corpus_file)
    assert len(BLOCK.findall((out / LOG_NAME).read_text())) == 2 * len(CATEGORIES)


def test_a_failed_login_is_an_eval_error_without_the_password(
    mongo: MongoDbContainer,
    loaded: MongoClient[dict[str, Any]],
    corpus_file: Path,
    tmp_path: Path,
) -> None:
    wrong = secrets.token_hex(8)
    with pytest.raises(EvalError, match="describe failed") as error:
        describe(_settings(mongo, wrong), tmp_path / "eval", corpus_file)
    assert wrong not in str(error.value)
    assert not (tmp_path / "eval" / DESCRIPTION_NAME).exists()
