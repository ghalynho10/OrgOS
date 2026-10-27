"""`scripts/fetch_data.sh` against a local Range handler, with only the standard tools on PATH.

The tests source the script and call `fetch_dataset` with a local URL, so
nothing reaches the network. Each run gets an empty folder, no `.env`, and a
`PATH` holding only bash, curl, md5sum (or md5), wc, date, mkdir and mv.
"""

import hashlib
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_data.sh"
NAME = "tiny dataset.zip"
PAYLOAD = os.urandom(300_000)
PAYLOAD_MD5 = hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest()
TOOLS = ("bash", "curl", "wc", "date", "mkdir", "mv")
PINNED_MD5 = "02f85309d966092ea130ca0797aea795"

pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed")


@dataclass
class Server:
    """What the handler serves, and the Range header of every request it saw."""

    url: str
    body: bytes = PAYLOAD
    cut_after: int | None = None
    requests: list[str | None] = field(default_factory=list)


@pytest.fixture
def server() -> Iterator[Server]:
    state = Server(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            header = self.headers.get("Range")
            state.requests.append(header)
            start = 0
            if header is not None:
                match = re.fullmatch(r"bytes=(\d+)-", header)
                assert match, header
                start = int(match.group(1))
                if start >= len(state.body):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(state.body)}")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(state.body) - 1}/{len(state.body)}"
                )
            else:
                self.send_response(200)
            rest = state.body[start:]
            self.send_header("Content-Length", str(len(rest)))
            self.end_headers()
            if state.cut_after is not None:
                # Drop the connection partway, like a network break.
                self.wfile.write(rest[: state.cut_after])
                self.close_connection = True
                state.cut_after = None
                return
            self.wfile.write(rest)

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{httpd.server_address[1]}/files/tiny.zip?download=1"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(scope="module")
def tool_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A folder holding only the standard tools the script may use."""
    folder = tmp_path_factory.mktemp("bin")
    md5_tool = "md5sum" if shutil.which("md5sum") else "md5"
    for tool in (*TOOLS, md5_tool):
        found = shutil.which(tool)
        assert found, tool
        (folder / tool).symlink_to(found)
    return folder


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _fetch(
    tool_bin: Path, cwd: Path, data_dir: Path, url: str, md5: str = PAYLOAD_MD5
) -> subprocess.CompletedProcess[str]:
    command = 'source "$1"; fetch_dataset "$2" "$3" "$4" "$5" "$6"'
    argv = [str(tool_bin / "bash"), "-c", command, "fetch", str(SCRIPT)]
    argv += [str(data_dir), url, NAME, md5, str(len(PAYLOAD))]
    env = {"PATH": str(tool_bin), "HOME": str(cwd)}
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _receipt(data_dir: Path) -> dict[str, str]:
    lines = (data_dir / "dataset.receipt").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


def _files(data_dir: Path) -> list[str]:
    return sorted(path.name for path in data_dir.iterdir())


# covers: AC-1, AC-6, AC-7
def test_a_good_file_in_place_is_reused_without_a_request(
    server: Server, tool_bin: Path, tmp_path: Path, data_dir: Path
) -> None:
    data_dir.mkdir()
    (data_dir / NAME).write_bytes(PAYLOAD)
    result = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert result.returncode == 0, result.stderr
    assert server.requests == []
    receipt = _receipt(data_dir)
    assert list(receipt) == ["file", "size", "md5", "record", "version", "url", "verified_at"]
    assert receipt["file"] == NAME
    assert receipt["size"] == str(len(PAYLOAD))
    assert receipt["md5"] == PAYLOAD_MD5
    assert receipt["record"] == "15719919"
    assert receipt["version"] == "2025-06-23"
    assert receipt["url"] == server.url
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", receipt["verified_at"])
    assert _files(data_dir) == ["dataset.receipt", NAME]


# covers: AC-2, AC-3, AC-6, AC-7
def test_an_interrupted_download_resumes_with_a_range_request(
    server: Server, tool_bin: Path, tmp_path: Path, data_dir: Path
) -> None:
    server.cut_after = 100_000
    first = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert first.returncode == 1
    assert "run again to resume" in first.stderr
    # Until the md5 passes, no file carries the final name.
    assert _files(data_dir) == [f"{NAME}.part"]
    partial = (data_dir / f"{NAME}.part").stat().st_size
    assert 0 < partial < len(PAYLOAD)

    second = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert second.returncode == 0, second.stderr
    assert server.requests == [None, f"bytes={partial}-"]
    assert f"resuming {data_dir / NAME}.part at {partial}" in second.stderr
    assert (data_dir / NAME).read_bytes() == PAYLOAD
    assert _files(data_dir) == ["dataset.receipt", NAME]
    assert _receipt(data_dir)["md5"] == PAYLOAD_MD5


# covers: AC-2
def test_a_fresh_download_renames_only_after_the_md5_passes(
    server: Server, tool_bin: Path, tmp_path: Path, data_dir: Path
) -> None:
    result = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert result.returncode == 0, result.stderr
    assert server.requests == [None]
    assert _files(data_dir) == ["dataset.receipt", NAME]


# covers: AC-4
def test_a_wrong_file_in_place_is_refused_and_left_untouched(
    server: Server, tool_bin: Path, tmp_path: Path, data_dir: Path
) -> None:
    data_dir.mkdir()
    junk = data_dir / NAME
    junk.write_bytes(b"not the dataset")
    before = junk.stat()
    result = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert result.returncode == 1
    assert hashlib.md5(b"not the dataset", usedforsecurity=False).hexdigest() in result.stderr
    assert PAYLOAD_MD5 in result.stderr
    assert server.requests == []
    assert junk.read_bytes() == b"not the dataset"
    assert junk.stat().st_mtime_ns == before.st_mtime_ns
    assert _files(data_dir) == [NAME]


# covers: AC-5
def test_a_full_size_part_is_checked_without_a_request(
    server: Server, tool_bin: Path, tmp_path: Path, data_dir: Path
) -> None:
    data_dir.mkdir()
    (data_dir / f"{NAME}.part").write_bytes(PAYLOAD)
    result = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert result.returncode == 0, result.stderr
    assert server.requests == []
    assert _files(data_dir) == ["dataset.receipt", NAME]


# covers: AC-5
def test_a_full_size_part_with_a_wrong_md5_stays_a_part(
    server: Server, tool_bin: Path, tmp_path: Path, data_dir: Path
) -> None:
    data_dir.mkdir()
    damaged = bytes([PAYLOAD[0] ^ 1]) + PAYLOAD[1:]
    (data_dir / f"{NAME}.part").write_bytes(damaged)
    result = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert result.returncode == 1
    assert hashlib.md5(damaged, usedforsecurity=False).hexdigest() in result.stderr
    assert PAYLOAD_MD5 in result.stderr
    assert f"Delete {data_dir / NAME}.part" in result.stderr
    assert server.requests == []
    assert _files(data_dir) == [f"{NAME}.part"]


# covers: AC-5
def test_a_finished_download_with_a_wrong_md5_writes_no_receipt(
    server: Server, tool_bin: Path, tmp_path: Path, data_dir: Path
) -> None:
    server.body = bytes(len(PAYLOAD))
    result = _fetch(tool_bin, tmp_path, data_dir, server.url)
    assert result.returncode == 1
    assert PAYLOAD_MD5 in result.stderr
    assert "Delete " in result.stderr
    assert _files(data_dir) == [f"{NAME}.part"]


# covers: AC-4, AC-7 (the script run directly, with its own pinned facts)
def test_run_directly_it_uses_the_pinned_facts_and_the_data_argument(
    tool_bin: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "elsewhere"
    data_dir.mkdir()
    (data_dir / "2025-06-23 ThePublicJiraDataset.zip").write_bytes(b"junk")
    env = {"PATH": str(tool_bin), "HOME": str(tmp_path)}
    result = subprocess.run(
        [str(tool_bin / "bash"), str(SCRIPT), str(data_dir)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert f"the pinned md5 is {PINNED_MD5}" in result.stderr
    assert not (data_dir / "dataset.receipt").exists()


# covers: AC-8 (the pins live in the script only)
def test_no_pins_under_src_orgos() -> None:
    for path in sorted((ROOT / "src" / "orgos").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert PINNED_MD5 not in text, path
            assert "zenodo.org" not in text, path
    assert PINNED_MD5 in SCRIPT.read_text()
