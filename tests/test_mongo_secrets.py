"""The `orgos mongo` failure paths show no password and keep stdout plain.

Runs the real CLI with no `docker` on PATH and Mongo unreachable, so every
check fails at once without any container.
"""

import hashlib
import socket
from pathlib import Path

import pytest

import orgos.mongostore.connect
from orgos.cli import run
from orgos.config import Settings
from orgos.mongostore.receipt import RECEIPT_NAME
from tests.test_mongostore import damaged_zip

SECRETS = {
    "POSTGRES_PASSWORD": "pg-super-Zx81",
    "ORGOS_PG_OWNER_PASSWORD": "pg-owner-Zx82",
    "ORGOS_PG_READER_PASSWORD": "pg-reader-Zx83",
    "MONGO_INITDB_ROOT_PASSWORD": "mongo-root-Zx84",
    "ORGOS_MONGO_READER_PASSWORD": "mongo-reader-Zx85",
    "NEO4J_AUTH": "neo4j/graph-Zx86",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(autouse=True)
def _nothing_reachable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ORGOS_MONGO_PORT", str(_free_port()))
    monkeypatch.setenv("ORGOS_DATA_DIR", str(tmp_path / "data"))
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setattr(orgos.mongostore.connect, "SERVER_SELECTION_TIMEOUT_MS", 300)
    monkeypatch.chdir(tmp_path)


def _run(args: list[str]) -> int | str | None:
    with pytest.raises(SystemExit) as excinfo:
        run(args)
    return excinfo.value.code


# covers: AC-11, AC-18, AC-26, AC-38
@pytest.mark.parametrize(
    "args",
    [["mongo", "restore"], ["mongo", "restore", "--check"], ["mongo", "restore", "--reset"]],
)
def test_restore_lists_every_block_without_secrets(
    args: list[str], capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    assert _run(args) == 3
    out, err = capfd.readouterr()
    # Under pytest the check lines reach caplog instead of the stderr handler.
    logged = caplog.text
    assert out == ""
    assert "restore blocked by 3 check(s), nothing was written:" in err
    assert "  container: docker is not installed or not on PATH" in err
    assert "  host login: login to Mongo from this machine failed: ServerSelection" in err
    assert "  receipt: no dataset.receipt in " in err
    assert "SKIP\tmongorestore\tnot checked (needs container)" in caplog.messages
    assert "Traceback" not in err
    assert "\x1b" not in out + err + logged
    for secret in [*SECRETS.values(), "graph-Zx86"]:
        assert secret not in out + err + logged


# covers: AC-11, AC-38
def test_verify_fails_with_exit_3_and_no_secrets(capfd: pytest.CaptureFixture[str]) -> None:
    assert _run(["mongo", "verify"]) == 3
    out, err = capfd.readouterr()
    assert out == ""
    assert err.startswith("verify failed: ServerSelectionTimeoutError: ")
    assert err.count("\n") == 1
    for secret in SECRETS.values():
        assert secret not in out + err


# covers: AC-11, AC-18
def test_a_damaged_member_is_a_header_block_listed_with_the_others(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    zip_path = damaged_zip(data_dir / "tiny.zip")
    body = zip_path.read_bytes()
    receipt = {
        "file": zip_path.name,
        "size": str(len(body)),
        "md5": hashlib.md5(body, usedforsecurity=False).hexdigest(),
        "record": "1",
        "version": "v",
        "url": "http://127.0.0.1/tiny.zip",
        "verified_at": "2026-10-28T12:00:00Z",
    }
    lines = "".join(f"{key}={value}\n" for key, value in receipt.items())
    (data_dir / RECEIPT_NAME).write_text(lines)
    assert _run(["mongo", "restore", "--check"]) == 3
    out, err = capfd.readouterr()
    assert out == ""
    assert "restore blocked by 3 check(s), nothing was written:" in err
    assert "  container: docker is not installed or not on PATH" in err
    assert "  host login: " in err
    assert "  header: cannot read the archive header: Error -3 while decompressing" in err
    assert "Traceback" not in err


# covers: AC-38
def test_docker_error_text_is_masked(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    docker = tmp_path / "bin" / "docker"
    root = SECRETS["MONGO_INITDB_ROOT_PASSWORD"]
    reader = SECRETS["ORGOS_MONGO_READER_PASSWORD"]
    docker.write_text(f"#!/bin/sh\necho 'daemon said {root} and {reader}' >&2\nexit 1\n")
    docker.chmod(0o755)
    assert _run(["mongo", "restore", "--check"]) == 3
    out, err = capfd.readouterr()
    assert "  container: `docker ps` exited 1: daemon said *** and ***" in err
    for secret in (root, reader):
        assert secret not in out + err + caplog.text
