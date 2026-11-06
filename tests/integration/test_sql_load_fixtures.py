"""The SQL load chain on the 270 corpus fixtures, on a real Postgres (spec 0009 AC-24).

The real dlt hints (`load_raw`) and the real dbt project (`run_dbt`) build raw,
`stg`, `model_int` and `model` from `tests/fixtures/corpus/`, read from the
files instead of Mongo. The good set adds two deliberate documents, named
below: an identical second copy, to prove the collapse, and one parent inside
the fixtures, which the draw happens to hold none of. Each broken copy then
stops the build with a message naming the key. The figure gate is never run
here: it is for the full corpus only (AC-15).

The tests share one database and run in file order: the good build first,
the broken builds after it.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from bson import ObjectId, json_util
from pydantic import SecretStr
from pymongo import MongoClient
from testcontainers.community.mongodb import MongoDbContainer

from orgos.config import Settings, dbt_subprocess_env
from orgos.ingest import CORPUS_PROJECTS, IngestError, to_raw_row
from orgos.ingest.load import COLLECTION, load_raw
from orgos.mongostore.dump import DATABASE
from orgos.sqlstore.bootstrap import bootstrap
from orgos.sqlstore.dbt import DBT_ENTRY, failure_report, read_run_results, run_dbt
from orgos.sqlstore.figures import CORPUS_FILE, KNOWN_ANSWERS_RECORD, load_consistency
from orgos.sqlstore.load import load_sql
from orgos.sqlstore.record import RUN_RECORD

from .conftest import MONGO_ROOT_PASSWORD, MONGO_ROOT_USER

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
DBT_DIR = REPO / "dbt"
FIXTURES = REPO / "tests" / "fixtures" / "corpus"

MODEL_TABLES = (
    "issue",
    "project",
    "person",
    "fix_version",
    "fixed_in",
    "issue_link",
    "logged_time",
)

# The identical second copy: a higher _id than every real one, so it is dropped.
DUPLICATED = "CLOV-600"
DUPLICATE_ID = ObjectId("ff" * 12)
# The added parent: CHILD gets PARENT, both fixture issues, not linked to each other.
CHILD, PARENT = "CLOUD-516", "CLOUD-1411"
# Link 400147 is held by both ends: BAM-3918 lists outwardIssue BAM-3938.
LINK_ID, LINK_OUTWARD_HOLDER, LINK_OTHER = "400147", "BAM-3918", "BAM-3938"
# A time entry: history 187266 of BAM-2346 holds one timespent item.
TIMED, HISTORY = "BAM-2346", "187266"
# The one fixture issue with a duedate, a plain date.
DUE, DUE_DATE = "CLOUD-391", datetime(2008, 2, 10, tzinfo=UTC)
# The load IDs dbt last wrote, read before and after a failed dlt load.
LOAD_IDS_BUILT = (
    "SELECT DISTINCT load_id FROM model_int.issue UNION SELECT DISTINCT load_id FROM model.issue"
)
# Any fixture issue with a priority and an offset on `created`.
PLAIN = "BAM-4235"


def _fixture_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name != "manifest.json":
            docs.extend(json_util.loads(path.read_text(encoding="utf-8")))
    return docs


def _doc(docs: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next(doc for doc in docs if doc["key"] == key and doc["_id"] != DUPLICATE_ID)


def _good_docs() -> list[dict[str, Any]]:
    """The 270 fixtures, one parent added inside them, and the identical copy last."""
    docs = _fixture_docs()
    child, parent = _doc(docs, CHILD), _doc(docs, PARENT)
    assert not child["fields"].get("parent")
    child["fields"]["parent"] = {"id": parent["id"], "key": PARENT}
    copy = deepcopy(_doc(docs, DUPLICATED))
    copy["_id"] = DUPLICATE_ID
    return [*docs, copy]


def _history(doc: dict[str, Any], history_id: str) -> dict[str, Any]:
    histories: list[dict[str, Any]] = doc["changelog"]["histories"]
    return next(history for history in histories if history["id"] == history_id)


def _link_entry(doc: dict[str, Any], link_id: str) -> dict[str, Any]:
    links: list[dict[str, Any]] = doc["fields"]["issuelinks"]
    return next(link for link in links if link["id"] == link_id)


@dataclass(frozen=True)
class Build:
    """One load and build: whether dbt passed, its failure report, the dlt load ID."""

    passed: bool
    report: str
    load_id: str


def _build(settings: Settings, docs: list[dict[str, Any]], pipelines: Path) -> Build:
    load_id = load_raw(settings.pg_owner_url, (to_raw_row(doc) for doc in docs), pipelines)
    passed = run_dbt(settings, DBT_DIR)
    report = ""
    if not passed:
        with psycopg.connect(settings.pg_conninfo("owner")) as conn:
            report = failure_report(read_run_results(DBT_DIR), conn)
    return Build(passed=passed, report=report, load_id=load_id)


def _dbt(settings: Settings, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            *(sys.executable, "-c", DBT_ENTRY, *args, "--no-use-colors"),
            *("--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)),
        ],
        env=dbt_subprocess_env(settings),
        capture_output=True,
        text=True,
        check=False,
    )


def _rows(settings: Settings, query: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(settings.pg_conninfo("owner")) as conn:
        return conn.execute(query).fetchall()


def _scalar(settings: Settings, query: str) -> Any:
    return _rows(settings, query)[0][0]


@pytest.fixture(scope="module")
def fx(pg_settings: Settings) -> Settings:
    """Its own database and roles, so the safety test's probe tables never meet it."""
    settings = pg_settings.model_copy(
        update={
            "pg_database": "orgos_fixtures",
            "pg_owner_user": "fixture_owner",
            "pg_owner_password": SecretStr(secrets.token_hex(8)),
            "pg_reader_user": "fixture_reader",
            "pg_reader_password": SecretStr(secrets.token_hex(8)),
        }
    )
    bootstrap(settings)
    return settings


@pytest.fixture(scope="module")
def pipelines(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("dlt")


@pytest.fixture(scope="module")
def good(fx: Settings, pipelines: Path) -> tuple[Build, Build]:
    """The good set built twice, so the second build replaces tables that hold keys."""
    first = _build(fx, _good_docs(), pipelines)
    second = _build(fx, _good_docs(), pipelines)
    return first, second


@pytest.fixture
def reader(fx: Settings, good: tuple[Build, Build]) -> Iterator[psycopg.Connection]:
    with psycopg.connect(fx.pg_conninfo("reader"), autocommit=True) as conn:
        yield conn


# The good build.


# covers: spec 0009 AC-3, AC-6, AC-17 (a rerun leaves one load ID everywhere)
def test_a_rerun_leaves_one_load_id_everywhere(fx: Settings, good: tuple[Build, Build]) -> None:
    first, second = good
    assert first.passed, first.report
    assert second.passed, second.report
    assert first.load_id != second.load_id
    assert _rows(fx, "SELECT DISTINCT _dlt_load_id FROM raw.jira_issue") == [(second.load_id,)]
    with psycopg.connect(fx.pg_conninfo("owner")) as conn:
        consistency = load_consistency(conn)
    assert consistency.problems == ()
    assert consistency.latest_load_id == second.load_id
    assert len(consistency.tables) == 1 + 12 + 7


# covers: spec 0009 AC-10, AC-11 (the good fixtures produce the expected rows)
def test_the_good_fixtures_produce_the_expected_rows(
    fx: Settings, good: tuple[Build, Build]
) -> None:
    docs = _fixture_docs()
    keys = {doc["key"] for doc in docs}
    links = {
        link["id"]
        for doc in docs
        for link in doc["fields"].get("issuelinks") or ()
        if (link.get("outwardIssue") or link.get("inwardIssue"))["key"] in keys
    }
    entries = {
        history["id"]
        for doc in docs
        for history in (doc.get("changelog") or {}).get("histories") or ()
        if any(
            item.get("field") == "timespent" and item.get("fieldtype") == "jira"
            for item in history["items"]
        )
    }
    assigned = sum(1 for doc in docs if (doc["fields"].get("assignee") or {}).get("key"))
    assert _scalar(fx, "SELECT count(*) FROM raw.jira_issue") == 271
    for table in ("model_int.issue", "model.issue"):
        assert _scalar(fx, f"SELECT count(*) FROM {table}") == 270
    for table in ("model_int.project", "model.project"):
        assert _scalar(fx, f"SELECT count(*) FROM {table}") == len(CORPUS_PROJECTS)
    for table in ("model_int.issue_link", "model.issue_link"):
        assert _scalar(fx, f"SELECT count(*) FROM {table}") == len(links)
    for table in ("model_int.logged_time", "model.logged_time"):
        assert _scalar(fx, f"SELECT count(*) FROM {table}") == len(entries)
    assert _scalar(fx, "SELECT count(*) FROM model_int.assigned_to") == assigned
    assert _scalar(fx, "SELECT count(*) FROM model_int.has_parent") == 1
    for table in MODEL_TABLES:
        assert _scalar(fx, f"SELECT count(*) FROM model.{table}") > 0, table


# covers: spec 0009 AC-7 (identical copies collapse to the lowest mongo_id)
def test_an_identical_copy_is_dropped_and_listed(fx: Settings, good: tuple[Build, Build]) -> None:
    original = str(_doc(_fixture_docs(), DUPLICATED)["_id"])
    assert _rows(
        fx, "SELECT key, kept_mongo_id, dropped_mongo_id FROM stg.stg_issue_doc_dropped"
    ) == [(DUPLICATED, original, str(DUPLICATE_ID))]
    assert _rows(
        fx, f"SELECT source_doc_id FROM model_int.issue WHERE issue_key = '{DUPLICATED}'"
    ) == [(original,)]


# covers: spec 0006 AC-8, spec 0007 AC-14 (no assignee means no edge and a null column)
def test_an_issue_with_no_assignee_has_no_edge_and_a_null_key(
    fx: Settings, good: tuple[Build, Build]
) -> None:
    unassigned = {
        doc["key"]
        for doc in _fixture_docs()
        if not (doc["fields"].get("assignee") or {}).get("key")
    }
    assert unassigned
    nulls = _rows(fx, "SELECT issue_key FROM model.issue WHERE assignee_key IS NULL")
    assert {key for (key,) in nulls} == unassigned
    assert (
        _scalar(
            fx,
            "SELECT count(*) FROM model_int.assigned_to WHERE src_id IN "
            "(SELECT issue_key FROM model.issue WHERE assignee_key IS NULL)",
        )
        == 0
    )


# covers: spec 0006 AC-9, spec 0007 AC-6 (one row per link, src on the outward side)
def test_a_link_held_by_both_issues_is_one_row_read_from_the_outward_side(
    fx: Settings, good: tuple[Build, Build]
) -> None:
    docs = _fixture_docs()
    assert "outwardIssue" in _link_entry(_doc(docs, LINK_OUTWARD_HOLDER), LINK_ID)
    assert "inwardIssue" in _link_entry(_doc(docs, LINK_OTHER), LINK_ID)
    assert _rows(
        fx, f"SELECT src_id, dst_id FROM model_int.issue_link WHERE link_id = '{LINK_ID}'"
    ) == [(LINK_OUTWARD_HOLDER, LINK_OTHER)]
    assert _rows(
        fx,
        f"SELECT from_issue_key, to_issue_key FROM model.issue_link WHERE link_id = '{LINK_ID}'",
    ) == [(LINK_OUTWARD_HOLDER, LINK_OTHER)]


# covers: spec 0006 AC-12 (a parent is child to parent in has_parent, never a link)
def test_a_parent_is_only_in_has_parent_from_child_to_parent(
    fx: Settings, good: tuple[Build, Build]
) -> None:
    assert _rows(fx, "SELECT src_id, dst_id FROM model_int.has_parent") == [(CHILD, PARENT)]
    assert _rows(fx, "SELECT issue_key FROM model.issue WHERE parent_key IS NOT NULL") == [(CHILD,)]
    assert (
        _scalar(
            fx,
            "SELECT count(*) FROM model_int.issue_link "
            f"WHERE (src_id, dst_id) IN (('{CHILD}', '{PARENT}'), ('{PARENT}', '{CHILD}'))",
        )
        == 0
    )


# covers: spec 0006 AC-30 (a person seen only as a time entry author has no attributes)
def test_an_author_with_no_role_has_null_attributes(
    fx: Settings, good: tuple[Build, Build]
) -> None:
    docs = _fixture_docs()
    roles = {
        (doc["fields"].get(role) or {}).get("key")
        for doc in docs
        for role in ("assignee", "reporter", "creator")
    } - {None}
    authors = {
        history["author"]["key"]
        for doc in docs
        for history in (doc.get("changelog") or {}).get("histories") or ()
        if "author" in history
        and any(
            item.get("field") == "timespent" and item.get("fieldtype") == "jira"
            for item in history["items"]
        )
    }
    nulls = _rows(
        fx, "SELECT person_key FROM model.person WHERE login_name IS NULL AND is_active IS NULL"
    )
    assert authors - roles
    assert {key for (key,) in nulls} == authors - roles
    assert _scalar(fx, "SELECT count(*) FROM model.person WHERE login_name IS NOT NULL") == len(
        roles
    )


# covers: spec 0006 AC-18, AC-29, spec 0009 AC-14 (a plain date is midnight UTC)
def test_a_plain_duedate_is_midnight_utc_under_the_build_zone(
    fx: Settings, good: tuple[Build, Build]
) -> None:
    assert _doc(_fixture_docs(), DUE)["fields"]["duedate"] == "2008-02-10"
    assert _rows(fx, f"SELECT due_at FROM model.issue WHERE issue_key = '{DUE}'") == [(DUE_DATE,)]


# Access, spec 0009 line 229 (the reader rights of spec 0007 AC-19 and AC-22).


@pytest.mark.parametrize("table", MODEL_TABLES)
def test_the_reader_can_select_every_model_table(reader: psycopg.Connection, table: str) -> None:
    row = reader.execute(f"SELECT count(*) FROM model.{table}").fetchone()
    assert row is not None and row[0] > 0


@pytest.mark.parametrize(
    "table",
    ["model_int.issue", "raw.jira_issue", "stg.stg_issue_doc", "test_failures.model_keys"],
)
def test_the_reader_cannot_read_outside_model(reader: psycopg.Connection, table: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute(f"SELECT 1 FROM {table} LIMIT 1")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO model.project VALUES ('X', 'x', 'doc', 'load')",
        "UPDATE model.issue SET status_name = 'x'",
        "DELETE FROM model.issue_link",
        "TRUNCATE model.logged_time",
        "CREATE TABLE model.reader_table (x int)",
        "DROP TABLE model.person",
    ],
)
def test_the_reader_cannot_write_model(reader: psycopg.Connection, statement: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute(statement)


def test_the_reader_session_zone_is_utc(reader: psycopg.Connection) -> None:
    assert reader.execute("SHOW timezone").fetchone() == ("UTC",)


# One broken copy per rule kind: the build stops and the message names the key.


def _broken(fx: Settings, pipelines: Path, change: Callable[[list[dict[str, Any]]], None]) -> Build:
    docs = _good_docs()
    change(docs)
    build = _build(fx, docs, pipelines)
    assert not build.passed
    return build


# covers: spec 0009 AC-8, spec 0006 AC-20 (differing copies of one key)
def test_differing_copies_stop_the_build_naming_the_key(
    fx: Settings, pipelines: Path, good: tuple[Build, Build]
) -> None:
    def change(docs: list[dict[str, Any]]) -> None:
        docs[-1]["fields"]["summary"] = "a second version"

    report = _broken(fx, pipelines, change).report
    assert "stg_issue_doc_copies_differ" in report
    assert DUPLICATED in report


# covers: spec 0006 AC-10 (the two views of a link disagree)
def test_disagreeing_link_views_stop_the_build_naming_the_link(
    fx: Settings, pipelines: Path, good: tuple[Build, Build]
) -> None:
    def change(docs: list[dict[str, Any]]) -> None:
        _link_entry(_doc(docs, LINK_OTHER), LINK_ID)["type"]["name"] = "Relate"

    report = _broken(fx, pipelines, change).report
    assert "stg_link_views_disagree" in report
    assert LINK_ID in report


# covers: spec 0006 AC-11, spec 0009 AC-17 (an 18th link type; a build that stops partway)
def test_an_unknown_link_type_stops_the_build_and_leaves_mixed_load_ids(
    fx: Settings, pipelines: Path, good: tuple[Build, Build]
) -> None:
    def change(docs: list[dict[str, Any]]) -> None:
        for key in (LINK_OUTWARD_HOLDER, LINK_OTHER):
            _link_entry(_doc(docs, key), LINK_ID)["type"]["name"] = "Teleports"

    build = _broken(fx, pipelines, change)
    assert "accepted_values_int_issue_link" in build.report
    assert "Teleports" in build.report
    # issue_link was built from the new load and failed its test, so model.issue_link
    # was skipped and still holds an older load: the check names it alone.
    with psycopg.connect(fx.pg_conninfo("owner")) as conn:
        consistency = load_consistency(conn)
    assert consistency.latest_load_id == build.load_id
    assert len(consistency.problems) == 1
    assert consistency.problems[0].startswith("model.issue_link holds load IDs ")
    assert consistency.problems[0].endswith(f"expected {build.load_id}")


# covers: spec 0006 AC-16 (a time entry with no author)
def test_an_entry_with_no_author_stops_the_build_naming_the_issue(
    fx: Settings, pipelines: Path, good: tuple[Build, Build]
) -> None:
    def change(docs: list[dict[str, Any]]) -> None:
        del _history(_doc(docs, TIMED), HISTORY)["author"]

    report = _broken(fx, pipelines, change).report
    assert "stg_time_entry_without_author" in report
    assert f"{TIMED} | {HISTORY}" in report


# covers: spec 0006 AC-19 (an empty string in a used column)
def test_an_empty_string_stops_the_build_naming_the_issue(
    fx: Settings, pipelines: Path, good: tuple[Build, Build]
) -> None:
    def change(docs: list[dict[str, Any]]) -> None:
        priority = _doc(docs, PLAIN)["fields"]["priority"]
        assert priority["name"]
        priority["name"] = ""

    report = _broken(fx, pipelines, change).report
    assert "no_empty_strings_int_issue_issue_key" in report
    assert f"priority_name | {PLAIN}" in report


# covers: spec 0006 AC-18, AC-29 (an instant with no offset)
def test_a_time_with_no_offset_stops_the_build_naming_the_issue(
    fx: Settings, pipelines: Path, good: tuple[Build, Build]
) -> None:
    def change(docs: list[dict[str, Any]]) -> None:
        fields = _doc(docs, PLAIN)["fields"]
        assert fields["created"].endswith("+0000")
        fields["created"] = fields["created"].removesuffix("+0000")

    report = _broken(fx, pipelines, change).report
    assert "stg_instant_offsets" in report
    assert f"{PLAIN} | created" in report


# covers: spec 0006 AC-14 (an amount that is not an integer)
def test_an_amount_that_is_not_an_integer_stops_the_build_naming_the_entry(
    fx: Settings, pipelines: Path, good: tuple[Build, Build]
) -> None:
    def change(docs: list[dict[str, Any]]) -> None:
        item = next(
            item
            for item in _history(_doc(docs, TIMED), HISTORY)["items"]
            if item["field"] == "timespent"
        )
        item["to"] = item["toString"] = "3600.5"

    report = _broken(fx, pipelines, change).report
    assert "stg_time_amount_not_integer" in report
    assert f"{TIMED} | {HISTORY}" in report


# The keys and the compare after a good build.


# covers: spec 0007 AC-15 (rebuilding one referenced table alone drops keys)
def test_rebuilding_person_alone_fails_the_keys_test(fx: Settings, pipelines: Path) -> None:
    build = _build(fx, _good_docs(), pipelines)
    assert build.passed, build.report
    assert _dbt(fx, "run", "--select", "person").returncode == 0
    tested = _dbt(fx, "test", "--select", "model_keys")
    assert tested.returncode != 0
    assert sorted(
        _rows(
            fx, "SELECT problem, table_name, key_columns, ref_table FROM test_failures.model_keys"
        )
    ) == [
        ("missing", "issue", "assignee_key", "model.person"),
        ("missing", "issue", "creator_key", "model.person"),
        ("missing", "issue", "reporter_key", "model.person"),
        ("missing", "logged_time", "person_key", "model.person"),
    ]


# covers: spec 0007 AC-20, AC-21 (the compare shows a changed row on both sides)
def test_the_compare_names_a_row_changed_in_a_copy(fx: Settings, good: tuple[Build, Build]) -> None:
    with psycopg.connect(fx.pg_conninfo("owner"), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS test_failures.fix_version_copy")
        conn.execute(
            "CREATE TABLE test_failures.fix_version_copy AS SELECT * FROM model.fix_version"
        )
        changed = conn.execute(
            "UPDATE test_failures.fix_version_copy SET is_released = NOT is_released "
            "WHERE version_id = (SELECT min(version_id) FROM model.fix_version) "
            "RETURNING version_id"
        ).fetchone()
    assert changed is not None
    columns = ", ".join(
        f"'{name}': '{name}'"
        for name in ("version_id", "version_name", "is_released", "source_doc_id", "load_id")
    )
    shown = _dbt(
        fx,
        "show",
        "--inline",
        "{{ test_matches_model_int(model='test_failures.fix_version_copy', "
        f"int_model=ref('int_fix_version'), columns={{{columns}}}) }}}}",
    )
    with psycopg.connect(fx.pg_conninfo("owner"), autocommit=True) as conn:
        conn.execute("DROP TABLE test_failures.fix_version_copy")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    lines = [line for line in shown.stdout.splitlines() if f"| {changed[0]} " in line]
    assert len(lines) == 2
    assert any("only in model_int" in line for line in lines)
    assert any("only in model " in line for line in lines)


# The command: a failed dlt load stops `orgos load sql` before dbt.


def _mongo_settings(fx: Settings, mongo: MongoDbContainer) -> Settings:
    return fx.model_copy(
        update={
            "mongo_host": mongo.get_container_host_ip(),
            "mongo_port": int(mongo.get_exposed_port(27017)),
            "mongo_user": MONGO_ROOT_USER,
            "mongo_password": SecretStr(MONGO_ROOT_PASSWORD),
            "mongo_reader_user": "fixture_mongo_reader",
            "mongo_reader_password": SecretStr(secrets.token_hex(8)),
        }
    )


def _load_root(tmp_path: Path) -> Path:
    # The repo's dbt project and the two committed files the preflight reads;
    # the run record and the dlt folder land here, never in the repo.
    root = tmp_path / "root"
    for name in (CORPUS_FILE, KNOWN_ANSWERS_RECORD):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / name, root / name)
    (root / "dbt").symlink_to(DBT_DIR)
    return root


# covers: spec 0009 AC-6, AC-18 (a failed dlt load stops the command before dbt)
def test_a_failed_dlt_load_stops_the_command_before_dbt(
    fx: Settings, own_mongo: MongoDbContainer, tmp_path: Path
) -> None:
    settings = _mongo_settings(fx, own_mongo)
    docs = [doc for doc in _fixture_docs() if doc["fields"]["project"]["key"] == "CLOV"]
    # jsonb refuses a NUL character, a terminal data error, so dlt fails once.
    docs[0]["fields"]["summary"] = "a NUL \x00 inside"
    with MongoClient(settings.mongo_uri) as root_client:
        root_client[DATABASE][COLLECTION].insert_many(docs)
        root_client.admin.command(
            "createUser",
            settings.mongo_reader_user,
            pwd=settings.mongo_reader_password.get_secret_value(),
            roles=[{"role": "read", "db": DATABASE}],
        )
    before = _rows(
        fx,
        LOAD_IDS_BUILT,
    )
    root = _load_root(tmp_path)

    with pytest.raises(IngestError) as raised:
        load_sql(settings, root)

    assert "dlt load step failed" in str(raised.value)
    assert settings.pg_owner_password.get_secret_value() not in str(raised.value)
    record = json.loads((root / RUN_RECORD).read_text(encoding="utf-8"))
    assert (record["status"], record["failed_step"]) == ("fail", "dlt")
    assert record["dbt_tests"] == {}
    assert record["dlt_load_id"] is None
    # dbt never ran: model_int and model still hold the load of the last build.
    assert (
        _rows(
            fx,
            LOAD_IDS_BUILT,
        )
        == before
    )
