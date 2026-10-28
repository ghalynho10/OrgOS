"""Fast checks of the Mongo staging store: the pure core, the exit code, and the commands."""

import struct
import zipfile
from pathlib import Path

import bson
import pytest
from pydantic import SecretStr
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from orgos.cli import EXIT_CODES, run
from orgos.config import Settings
from orgos.mongostore import MongoStoreError, mongo_errors
from orgos.mongostore.dump import (
    ARCHIVE_MAGIC,
    ARCHIVE_MEMBER,
    DATABASE,
    EXPECTED_COUNTS,
    TruncatedHeader,
    parse_header,
)
from orgos.mongostore.receipt import RECEIPT_KEYS, parse_receipt
from orgos.mongostore.restore import (
    RESTORE_SCRIPT,
    mongorestore_argv,
    mongorestore_failure,
    stream,
)
from orgos.mongostore.verify import compare, failure_message, report_text
from tests.test_scaffold import REQUIRED


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    monkeypatch.chdir(tmp_path)


def _run(args: list[str]) -> int | str | None:
    with pytest.raises(SystemExit) as excinfo:
        run(args)
    return excinfo.value.code


# The commands


# covers: AC-9
@pytest.mark.parametrize(
    "args", [["mongo", "--help"], ["mongo", "restore", "--help"], ["mongo", "verify", "--help"]]
)
def test_mongo_help_needs_no_config(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(args) == 0
    assert "Usage: " in capsys.readouterr().out


# covers: AC-9
def test_top_level_help_lists_mongo(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["--help"]) == 0
    assert "mongo" in capsys.readouterr().out


# covers: AC-10
@pytest.mark.parametrize("args", [["mongo", "verify"], ["mongo", "restore", "--check"]])
def test_missing_reader_password_exits_2_and_names_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ORGOS_MONGO_READER_PASSWORD")
    assert _run(args) == 2
    err = capsys.readouterr().err
    assert "ORGOS_MONGO_READER_PASSWORD is not set" in err
    assert "Traceback" not in err


# covers: AC-11
def test_exit_codes_hold_the_mongo_row() -> None:
    assert (MongoStoreError, 3) in EXIT_CODES


# covers: AC-11
@pytest.mark.parametrize(
    "error",
    [
        ServerSelectionTimeoutError("127.0.0.1:1: connection refused"),
        OperationFailure("Authentication failed."),
        FileNotFoundError(2, "No such file or directory", "docker"),
        zipfile.BadZipFile("File is not a zip file"),
        PermissionError(13, "Permission denied", "data/dataset.receipt"),
    ],
)
def test_each_wrapped_error_exits_3_once_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    def broken(settings: Settings) -> None:
        with mongo_errors("verify"):
            raise error

    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("orgos.cli.verify", broken)
    assert _run(["mongo", "verify"]) == 3
    err = capsys.readouterr().err
    assert err.count("verify failed: ") == 1
    assert type(error).__name__ in err
    assert "Traceback" not in err


# covers: AC-11
def test_an_unnamed_error_keeps_its_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(settings: Settings) -> None:
        with mongo_errors("verify"):
            raise RuntimeError("not in the exit code table")

    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("orgos.cli.verify", broken)
    with pytest.raises(RuntimeError, match="not in the exit code table"):
        run(["mongo", "verify"])


# covers: AC-38
def test_wrapped_messages_mask_the_secrets() -> None:
    with pytest.raises(MongoStoreError) as excinfo, mongo_errors("restore", (SecretStr("s3cret"),)):
        raise OSError("login with s3cret refused")
    assert "s3cret" not in str(excinfo.value)
    assert "***" in str(excinfo.value)


# The archive header


def _header(prelude: dict[str, object], entries: list[dict[str, object]]) -> bytes:
    data = struct.pack("<I", ARCHIVE_MAGIC) + bson.encode(prelude)
    data += b"".join(bson.encode(entry) for entry in entries)
    return data + struct.pack("<i", -1) + b"body bytes that are never read"


PRELUDE = {"server_version": "7.0.14", "tool_version": "100.9.5", "version": "0.1"}


def _entry(name: str, db: str = DATABASE) -> dict[str, object]:
    return {"db": db, "collection": name, "metadata": "{}", "type": "collection", "size": 0}


# covers: AC-16
def test_header_is_parsed_from_bson_documents() -> None:
    header = parse_header(_header(PRELUDE, [_entry("Apache"), _entry("Qt", db="Other")]))
    assert header.server_version == "7.0.14"
    assert header.tool_version == "100.9.5"
    assert header.collections == {"Apache", "Qt"}
    assert header.databases == {DATABASE, "Other"}


def test_header_with_a_wrong_magic_is_rejected() -> None:
    data = _header(PRELUDE, [])
    with pytest.raises(MongoStoreError, match="magic"):
        parse_header(b"\x00\x00\x00\x00" + data[4:])


@pytest.mark.parametrize("cut", [2, 10, 60])
def test_a_cut_off_header_says_so(cut: int) -> None:
    data = _header(PRELUDE, [_entry("Apache")])
    with pytest.raises(TruncatedHeader):
        parse_header(data[:cut])


# The receipt


RECEIPT_TEXT = (
    "file=2025-06-23 ThePublicJiraDataset.zip\n"
    "size=5813135238\n"
    "md5=02f85309d966092ea130ca0797aea795\n"
    "record=15719919\n"
    "version=2025-06-23\n"
    "url=https://example.org/files/x.zip?download=1\n"
    "verified_at=2026-10-26T12:00:00Z\n"
)


def test_receipt_is_parsed_splitting_on_the_first_equals() -> None:
    receipt = parse_receipt(RECEIPT_TEXT)
    assert receipt.file == "2025-06-23 ThePublicJiraDataset.zip"
    assert receipt.size == 5813135238
    assert receipt.url == "https://example.org/files/x.zip?download=1"
    assert tuple(receipt.as_dict()) == RECEIPT_KEYS


# covers: AC-13
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (RECEIPT_TEXT + "extra=1\n", "unknown key 'extra'"),
        (RECEIPT_TEXT + "md5=abc\n", "key 'md5' twice"),
        (RECEIPT_TEXT.replace("record=15719919\n", ""), "missing record"),
        (RECEIPT_TEXT.replace("file=2025", "file=../2025"), "bare file name"),
        (RECEIPT_TEXT.replace("file=2025", "file=sub\\2025"), "bare file name"),
        (RECEIPT_TEXT.replace("size=5813135238", "size=big"), "not a whole number"),
        ("no equals sign\n", "not key=value"),
    ],
)
def test_malformed_receipts_name_the_problem(text: str, reason: str) -> None:
    with pytest.raises(MongoStoreError, match=reason):
        parse_receipt(text)


# The PASS rule and its report


# covers: AC-31
def test_expected_counts_match_the_spec_table() -> None:
    assert len(EXPECTED_COUNTS) == 16
    assert sum(EXPECTED_COUNTS.values()) == 2_686_282
    assert EXPECTED_COUNTS["Apache"] == 1_014_926
    assert EXPECTED_COUNTS["SecondLife"] == 1_867
    with pytest.raises(TypeError):
        EXPECTED_COUNTS["Apache"] = 0  # type: ignore[index]


# covers: AC-28, AC-29
def test_equal_names_and_counts_pass_ignoring_system_collections() -> None:
    comparison = compare({"A": 2, "B": 3}, {"B": 3, "A": 2, "system.views": 1})
    assert comparison.passed
    assert report_text(comparison) == "verify PASS\nA\t2\t2\tok\nB\t3\t3\tok\ntotal\t5\t5\tok\n"


# covers: AC-28, AC-29, AC-30
def test_every_difference_fails_and_is_listed() -> None:
    expected = {"Long": 2, "Missing": 1, "Short": 5, "Same": 4}
    live = {"Extra": 7, "Long": 3, "Same": 4, "Short": 4}
    comparison = compare(expected, live)
    assert not comparison.passed
    assert [(row.name, row.status) for row in comparison.differences] == [
        ("Long", "DIFF"),
        ("Missing", "MISSING"),
        ("Short", "DIFF"),
        ("Extra", "EXTRA"),
    ]
    assert report_text(comparison).splitlines() == [
        "verify FAIL",
        "Long\t2\t3\tDIFF",
        "Missing\t1\tnone\tMISSING",
        "Same\t4\t4\tok",
        "Short\t5\t4\tDIFF",
        "Extra\tnone\t7\tEXTRA",
        "total\t12\t18\tDIFF",
    ]
    assert failure_message(comparison, "the expected counts").startswith(
        "verify FAIL: 4 collections differ"
    )


def test_an_empty_database_fails_against_any_expectation() -> None:
    comparison = compare(EXPECTED_COUNTS, {})
    assert not comparison.passed
    assert len(comparison.differences) == 16


# The mongorestore call


# covers: AC-25
def test_the_exec_call_has_the_exact_flags_and_no_password() -> None:
    argv = mongorestore_argv("abc123")
    assert argv[:4] == ("docker", "exec", "-i", "abc123")
    assert argv[4:6] == ("sh", "-c")
    script = argv[6]
    assert script == RESTORE_SCRIPT
    assert script.startswith("exec mongorestore ")
    for flag in (
        "--gzip",
        "--archive",
        f'--nsInclude="{DATABASE}.*"',
        "--stopOnError",
        '--password="$MONGO_INITDB_ROOT_PASSWORD"',
    ):
        assert flag in script.split(), flag
    for banned in ("--drop", "--nsFrom", "--nsTo"):
        assert banned not in script
    for secret in REQUIRED.values():
        assert all(secret not in part for part in argv)


# covers: AC-27
def test_a_failed_mongorestore_names_its_status() -> None:
    assert "status 1;" in mongorestore_failure(1)
    assert "--reset" in mongorestore_failure(1)
    assert "ran out of memory" in mongorestore_failure(137)
    assert "memory" not in mongorestore_failure(1)


def damaged_zip(path: Path) -> Path:
    """A zip whose deflated member has an invalid block type, so reading it raises zlib.error."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ARCHIVE_MEMBER, b"orgos " * 50_000)
    data = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", data, 26)
    # Block type 11 (reserved) in the first deflate header.
    data[30 + name_length + extra_length] = 0xFF
    path.write_bytes(bytes(data))
    return path


# covers: AC-11
def test_a_damaged_member_fails_the_zip_check_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    # Stands in for `docker exec -i ... mongorestore`: reads the archive and discards it.
    docker.write_text("#!/bin/sh\nexec /bin/cat >/dev/null\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(MongoStoreError) as excinfo:
        stream("abc123", damaged_zip(tmp_path / "damaged.zip"), ARCHIVE_MEMBER)
    message = str(excinfo.value)
    assert message.startswith("the archive failed its zip check (Error -3 while decompressing")
    assert message.endswith(f"{DATABASE} is partial, rerun with --reset")
