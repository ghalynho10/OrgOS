"""The known answers' Mongo reads on a real Mongo holding fixture issues (spec 0005, AC-18, AC-21).

Three fixture projects are loaded and two of them are the corpus, so the
whole collection pass (bot flags, person field) and the corpus pass are told
apart. The reads go through a reader login made here and leave Mongo as it was.
The guards that need those reads are forced to fail here too (AC-43, AC-45).
"""

import json
import os
import secrets
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from bson import json_util
from pymongo import MongoClient
from testcontainers.community.mongodb import MongoDbContainer

from orgos.config import Settings, load_settings
from orgos.eval import EvalError
from orgos.eval.answers import answers, read_snapshot
from orgos.eval.describe import Corpus
from orgos.eval.entries import flag_bots, rebuild_entries
from orgos.eval.frozen import FROZEN, QUESTIONS, frozen_line
from orgos.eval.guards import SPIKE_RESULTS, Targets, reconciliation_checks
from orgos.eval.known import CORPUS_PROJECTS
from orgos.eval.snapshot import Collapsed, Snapshot
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


# The guards after the file guards, in the AC-43 order, through the real command code


def _git(root: Path, *args: str) -> None:
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def _pushed_question_q01(tmp_path: Path) -> Path:
    remote, root = tmp_path / "remote.git", tmp_path / "work"
    _git(tmp_path, "init", "--quiet", "--bare", str(remote))
    _git(tmp_path, "init", "--quiet", "--initial-branch=main", str(root))
    for key, value in (
        ("user.email", "test@example.invalid"),
        ("user.name", "test"),
        ("commit.gpgsign", "false"),
    ):
        _git(root, "config", key, value)
    _git(root, "remote", "add", "origin", str(remote))
    (root / "eval").mkdir()
    table = {
        "id": "Q01",
        "text": "<placeholder>",
        "type": "aggregate",
        "hops": 0,
        "hop_mode": "fixed",
        "shape": "map",
        "unit": "count",
        "fields": ["fields.created", "fields.resolution.name", "fields.assignee.key"],
        "reading": "<placeholder>",
        "data": "real",
    }
    lines = "".join(f"{key} = {json.dumps(value)}\n" for key, value in table.items())
    (root / QUESTIONS).write_text(f"[[question]]\n{lines}")
    (root / FROZEN).write_text(frozen_line(root, QUESTIONS) + "\n")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "step 1")
    _git(root, "push", "--quiet", "origin", "main")
    return root


def _write_spike_results(root: Path, snapshot: Snapshot, extra_seconds: int = 0) -> None:
    empty = Targets({}, {}, (), (), "")
    found = {check.name: check.found for check in reconciliation_checks(snapshot, empty)}
    results = root / SPIKE_RESULTS
    results.mkdir(parents=True, exist_ok=True)
    corpus = {
        "collection": "Jira",
        "group": list(snapshot.projects),
        "projects": [
            {
                "key": project,
                "positive_seconds": found["positive seconds per project"][project] + extra_seconds,
                "people": found["people per project"][project],
            }
            for project in snapshot.projects
        ],
    }
    (results / "corpus.json").write_text(json.dumps(corpus))
    (results / "flagged_accounts.csv").write_text(
        "collection,account\n" + "".join(f"Jira,{a}\n" for a in found["flagged accounts"])
    )
    project_of = {issue.key: issue.project_key for issue in snapshot.issues}
    (results / "entry_mismatches.csv").write_text(
        "collection,issue_key,project\n"
        + "".join(f"Jira,{k},{project_of[k]}\n" for k in found["entry mismatches"])
    )
    (results / "run_record.json").write_text(
        json.dumps({"run": {"person_id_field": {"Jira": snapshot.person_field}}})
    )


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts and "remote.git" not in path.parts
    }


def test_uniqueness_then_reconciliation_then_the_registry_stop_in_that_order(
    mongo: MongoDbContainer,
    loaded: MongoClient[dict[str, Any]],
    reader_password: str,
    tmp_path: Path,
) -> None:
    settings = _settings(mongo, reader_password)
    root = _pushed_question_q01(tmp_path)
    everything = Corpus("Jira", CORPUS_PROJECTS)
    right = read_snapshot(settings, everything, PATHS)
    assert right.entries  # the duplicate below needs a history that is a time entry

    # Every later guard is set to fail too, so only the order decides what stops the run.
    _write_spike_results(root, right, extra_seconds=1)
    jira = loaded[DATABASE].Jira
    source = jira.find_one({"key": right.entries[0].issue_key})
    assert source is not None
    copy = {name: value for name, value in source.items() if name != "_id"}
    copy_id = jira.insert_one({**copy, "key": f"{source['key']}9"}).inserted_id
    try:
        with pytest.raises(EvalError, match=r"^uniqueness: changelog history id once failed"):
            answers(settings, root, check=False, only_id=None)
    finally:
        jira.delete_one({"_id": copy_id})

    with pytest.raises(EvalError, match=r"^reconciliation: positive seconds per project failed"):
        answers(settings, root, check=False, only_id=None)

    _write_spike_results(root, right)
    before = _files(root)
    for check in (False, True):
        with pytest.raises(EvalError, match=r"^registry: no function for \[\]; registered but"):
            answers(settings, root, check=check, only_id=None)

    # AC-45: --only runs every guard but the registry, and writes nothing
    lines = answers(settings, root, check=False, only_id="Q01")
    assert any(line.startswith('{\n  "Q01": {') for line in lines)
    assert _files(root) == before

    with pytest.raises(EvalError, match=r"^use --check or --only, not both$"):
        answers(settings, root, check=True, only_id="Q01")
