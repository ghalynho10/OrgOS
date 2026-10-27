"""The restore end to end on a tiny archive in a real Mongo: stream, PASS, marker, reader.

Two small collections in a database named `JiraReposAnon` are dumped with
`mongodump --archive --gzip`, zipped under a member path, and restored
through the real `docker exec` call with injected expected counts.
"""

import hashlib
import os
import struct
import time
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from bson import Binary
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from testcontainers.community.mongodb import MongoDbContainer

import orgos.mongostore.restore as restore_module
from orgos.config import Settings, load_settings
from orgos.mongostore import MongoStoreError
from orgos.mongostore.dump import DATABASE
from orgos.mongostore.marker import MARKER_COLLECTION, MARKER_DATABASE
from orgos.mongostore.preflight import Limits
from orgos.mongostore.receipt import RECEIPT_KEYS, RECEIPT_NAME
from orgos.mongostore.restore import restore
from tests.integration.conftest import MONGO_ROOT_PASSWORD, MONGO_ROOT_USER

pytestmark = pytest.mark.integration

MEMBER = "Tiny/3. DataDump/mongodump-tiny.archive"
ZIP_NAME = "tiny dataset.zip"
TINY = {"Alpha": 3, "Beta": 2}
READER_USER = "orgos_raw_reader"
READER_PASSWORD = "test-mongo-reader"
RELAXED = Limits(min_free_disk=0, min_memory=0)
DUMP_SCRIPT = (
    'exec mongodump --username="$MONGO_INITDB_ROOT_USERNAME"'
    ' --password="$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase=admin'
    f" --db={DATABASE} --archive --gzip"
)


def _dump(mongo: MongoDbContainer) -> bytes:
    code, (out, err) = mongo.get_wrapped_container().exec_run(["sh", "-c", DUMP_SCRIPT], demux=True)
    assert code == 0, err
    return bytes(out)


def _zip_bytes(archive: bytes) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as zipped:
        zipped.writestr(MEMBER, archive)
    return buffer.getvalue()


def _damage_crc(zipped: bytes) -> bytes:
    """The same zip with the member's CRC wrong in both headers; the data is intact."""
    with zipfile.ZipFile(BytesIO(zipped)) as archive:
        crc = archive.getinfo(MEMBER).CRC
    good, bad = struct.pack("<I", crc), struct.pack("<I", crc ^ 0xFFFF)
    assert zipped.count(good) == 2
    return zipped.replace(good, bad)


def _place(data_dir: Path, zipped: bytes) -> None:
    for path in data_dir.iterdir():
        path.unlink()
    (data_dir / ZIP_NAME).write_bytes(zipped)
    receipt = {
        "file": ZIP_NAME,
        "size": str(len(zipped)),
        "md5": hashlib.md5(zipped, usedforsecurity=False).hexdigest(),
        "record": "0",
        "version": "test",
        "url": "http://127.0.0.1/tiny.zip?download=1",
        "verified_at": "2026-10-26T12:00:00Z",
    }
    (data_dir / RECEIPT_NAME).write_text("".join(f"{k}={v}\n" for k, v in receipt.items()))


@pytest.fixture(scope="module")
def mongo_settings(mongo: MongoDbContainer, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return load_settings(
        _env_file=None,
        ORGOS_MONGO_HOST=mongo.get_container_host_ip(),
        ORGOS_MONGO_PORT=int(mongo.get_exposed_port(27017)),
        MONGO_INITDB_ROOT_USERNAME=MONGO_ROOT_USER,
        MONGO_INITDB_ROOT_PASSWORD=MONGO_ROOT_PASSWORD,
        ORGOS_MONGO_READER_USER=READER_USER,
        ORGOS_MONGO_READER_PASSWORD=READER_PASSWORD,
        ORGOS_DATA_DIR=str(tmp_path_factory.mktemp("data")),
        POSTGRES_PASSWORD="unused",
        ORGOS_PG_OWNER_PASSWORD="unused",
        ORGOS_PG_READER_PASSWORD="unused",
        NEO4J_AUTH="neo4j/unused-password",
    )


@pytest.fixture(scope="module")
def root(mongo_settings: Settings) -> Iterator[MongoClient[dict[str, Any]]]:
    with MongoClient(mongo_settings.mongo_uri, tz_aware=True) as client:
        yield client


@pytest.fixture(scope="module")
def container_id(mongo: MongoDbContainer) -> str:
    return str(mongo.get_wrapped_container().id)


@pytest.fixture(scope="module")
def archives(mongo: MongoDbContainer, root: MongoClient[dict[str, Any]]) -> dict[str, bytes]:
    """Zips of the tiny dump, and of one whose document fails its collection's validator."""
    database = root[DATABASE]
    database.Alpha.insert_many([{"_id": n} for n in range(3)])
    # Random bytes keep the member well past the header read window, so only
    # the stream reads it to its end (where zipfile checks the CRC).
    database.Beta.insert_many([{"_id": n, "blob": Binary(os.urandom(400_000))} for n in range(2)])
    good = _dump(mongo)
    root.drop_database(DATABASE)
    database.create_collection("Gamma", validator={"$jsonSchema": {"required": ["x"]}})
    database.Gamma.insert_one({"_id": 1}, bypass_document_validation=True)
    invalid = _dump(mongo)
    root.drop_database(DATABASE)
    return {"good": _zip_bytes(good), "invalid": _zip_bytes(invalid)}


@pytest.fixture(autouse=True)
def fresh(
    root: MongoClient[dict[str, Any]], mongo_settings: Settings, archives: dict[str, bytes]
) -> None:
    root.drop_database(DATABASE)
    root.drop_database(MARKER_DATABASE)
    if root.admin.command("usersInfo", READER_USER)["users"]:
        root.admin.command("dropUser", READER_USER)
    _place(mongo_settings.data_dir, archives["good"])


def _restore(
    settings: Settings, container_id: str, *, reset: bool = False, check_only: bool = False
) -> None:
    restore(
        settings,
        reset=reset,
        check_only=check_only,
        container=container_id,
        expected=TINY,
        member=MEMBER,
        limits=RELAXED,
    )


def _marker(root: MongoClient[dict[str, Any]]) -> dict[str, Any] | None:
    return root[MARKER_DATABASE][MARKER_COLLECTION].find_one({"_id": DATABASE})


def _counts(root: MongoClient[dict[str, Any]]) -> dict[str, int]:
    database = root[DATABASE]
    return {name: database[name].count_documents({}) for name in database.list_collection_names()}


# covers: AC-24, AC-25, AC-26, AC-32, AC-33, AC-34
def test_tiny_restore_passes_and_writes_the_marker(
    mongo_settings: Settings,
    container_id: str,
    root: MongoClient[dict[str, Any]],
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    _restore(mongo_settings, container_id)
    out, err = capfd.readouterr()

    assert out == "verify PASS\nAlpha\t3\t3\tok\nBeta\t2\t2\tok\ntotal\t5\t5\tok\n"
    assert "\x1b" not in out
    # mongorestore read the archive from stdin and wrote its progress to stderr.
    assert "from `archive on stdin`" in err
    assert "5 document(s) restored successfully. 0 document(s) failed to restore." in err
    assert "restore complete: 2 collections verified, marker written" in caplog.text

    marker = _marker(root)
    assert marker is not None
    assert marker["counts"] == TINY
    assert tuple(marker["receipt"]) == RECEIPT_KEYS
    assert marker["receipt"]["file"] == ZIP_NAME
    assert marker["dump"]["server_version"] == "7.0.45"
    assert marker["dump"]["tool_version"].startswith("100.")
    assert marker["server_version"] == "7.0.45"
    assert marker["completed_at"].tzinfo is not None

    assert sorted(path.name for path in mongo_settings.data_dir.iterdir()) == sorted(
        [ZIP_NAME, RECEIPT_NAME]
    )

    with MongoClient(mongo_settings.mongo_reader_uri, serverSelectionTimeoutMS=5000) as reader:
        assert reader[DATABASE].Alpha.find_one({"_id": 0}) == {"_id": 0}
        with pytest.raises(OperationFailure, match="not authorized"):
            reader[DATABASE].Alpha.insert_one({"_id": 99})
        with pytest.raises(OperationFailure, match="not authorized"):
            reader[MARKER_DATABASE][MARKER_COLLECTION].find_one()


# covers: AC-27 (and confirms mongorestore exits non zero on a failed document)
def test_a_failed_document_stops_mongorestore_and_leaves_no_marker(
    mongo_settings: Settings,
    container_id: str,
    root: MongoClient[dict[str, Any]],
    archives: dict[str, bytes],
    capfd: pytest.CaptureFixture[str],
) -> None:
    _place(mongo_settings.data_dir, archives["invalid"])
    with pytest.raises(MongoStoreError, match=r"mongorestore exited with status 1;.*--reset"):
        restore(
            mongo_settings,
            reset=False,
            check_only=False,
            container=container_id,
            expected={"Gamma": 1},
            member=MEMBER,
            limits=RELAXED,
        )
    out, err = capfd.readouterr()
    assert "Document failed validation" in err
    assert out == ""
    assert _marker(root) is None


# covers: AC-24
def test_a_crc_error_at_the_end_of_the_stream_leaves_no_marker(
    mongo_settings: Settings,
    container_id: str,
    root: MongoClient[dict[str, Any]],
    archives: dict[str, bytes],
) -> None:
    _place(mongo_settings.data_dir, _damage_crc(archives["good"]))
    with pytest.raises(MongoStoreError, match=r"zip check \(Bad CRC-32.*--reset"):
        _restore(mongo_settings, container_id)
    assert _marker(root) is None


def _mongorestore_running(mongo: MongoDbContainer) -> bool:
    script = "cat /proc/[0-9]*/comm 2>/dev/null"
    _, out = mongo.get_wrapped_container().exec_run(["sh", "-c", script])
    return b"mongorestore" in out


# covers: AC-40
def test_ctrl_c_during_the_stream_stops_the_child_and_leaves_no_marker(
    mongo: MongoDbContainer,
    mongo_settings: Settings,
    container_id: str,
    root: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_chunks = restore_module._chunks

    def interrupted(zip_path: Path, member: str) -> Iterator[bytes]:
        chunks = real_chunks(zip_path, member)
        yield next(chunks)
        raise KeyboardInterrupt

    monkeypatch.setattr(restore_module, "CHUNK_SIZE", 64 * 1024)
    monkeypatch.setattr(restore_module, "_chunks", interrupted)
    with pytest.raises(MongoStoreError) as excinfo:
        _restore(mongo_settings, container_id)
    message = str(excinfo.value)
    assert message.count("\n") == 0
    assert "partial" in message
    assert "--reset" in message
    assert _marker(root) is None
    deadline = time.monotonic() + 30
    while _mongorestore_running(mongo):
        assert time.monotonic() < deadline, "mongorestore still runs 30 s after the interrupt"
        time.sleep(0.5)
