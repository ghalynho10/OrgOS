"""Shared shell for the spike 0001 scripts: the rules guard, the reader client, the results folder.

Every script starts with `guard()`, which refuses to run when `rules.py`
differs from HEAD (AC-1), and reads Mongo only through `reader()`, built
from the reader login (AC-2, AC-4). Throwaway spike code.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from orgos.config import Settings
from orgos.mongostore.dump import DATABASE, EXPECTED_COUNTS

SPIKE = Path(__file__).resolve().parent
RULES = SPIKE / "rules.py"
RESULTS = SPIKE / "results"
REPO = SPIKE.parents[1]

COLLECTIONS = tuple(sorted(EXPECTED_COUNTS))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def guard() -> dict[str, str]:
    """Stop unless `rules.py` is committed and unchanged; return its commit and blob hash."""
    relative = str(RULES.relative_to(REPO))
    if _git("status", "--porcelain", "--", relative):
        sys.exit(f"refusing to run: {relative} differs from HEAD (commit or revert it first)")
    blob = _git("rev-parse", f"HEAD:{relative}")
    if _git("hash-object", relative) != blob:
        sys.exit(f"refusing to run: {relative} differs from HEAD")
    commit = _git("log", "-1", "--format=%H", "--", relative)
    return {"rules_commit": commit, "rules_blob": blob}


def load_rules() -> ModuleType:
    """Import `rules.py` by path (spikes/ is not a package)."""
    spec = importlib.util.spec_from_file_location("spike0001_rules", RULES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def reader() -> Database[dict[str, Any]]:
    """The raw database through the read only login: the only connection the spike opens."""
    client: MongoClient[dict[str, Any]] = MongoClient(
        Settings().mongo_reader_uri, serverSelectionTimeoutMS=5000
    )
    return client[DATABASE]


def write_csv(name: str, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> Path:
    """Write one results CSV and return its path."""
    path = RESULTS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def update_record(section: str, data: Any) -> None:
    """Merge one section into `results/run_record.json` (no credentials ever go in)."""
    path = RESULTS / "run_record.json"
    record = json.loads(path.read_text()) if path.exists() else {}
    record[section] = data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def read_csv(name: str) -> list[dict[str, str]]:
    """Read one results CSV as rows of strings."""
    with (RESULTS / name).open(newline="") as handle:
        return list(csv.DictReader(handle))
