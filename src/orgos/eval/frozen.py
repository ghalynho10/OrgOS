"""The freeze check: `eval/FROZEN` pins the eval files by sha256 (spec 0005, AC-2, AC-3, AC-46).

`FROZEN` is in `sha256sum` format, one `<sha256>  <path>` line per pinned
file with the path relative to the repo root, so `shasum -a 256 -c eval/FROZEN`
passes from the root. `frozen_problems` is the rule a unit test applies to the
repo on every commit and in CI; once the `eval-frozen` tag exists, `FROZEN`
must also equal its tagged copy (AC-47). `orgos eval answers` reuses the hashing.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path, PurePosixPath

from orgos.eval import EvalError

FROZEN = "eval/FROZEN"
QUESTIONS = "eval/questions.toml"
ANSWERS = "eval/answers.json"
AMENDMENTS = "eval/amendments.toml"
TAG = "eval-frozen"

_LINE = re.compile(r"([0-9a-f]{64})  (\S.*)")
_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    """The hex sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_line(root: Path, path: str) -> str:
    """The `FROZEN` line for one file, as `sha256sum` writes it."""
    return f"{sha256_file(root / path)}  {path}"


def parse_frozen(text: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """The `(sha256, path)` pairs of a `FROZEN` text, and a problem per malformed line."""
    pins: list[tuple[str, str]] = []
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _LINE.fullmatch(line)
        if match is None:
            problems.append(f"{FROZEN} line {number}: not `<sha256>  <path>`")
            continue
        path = match.group(2)
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            problems.append(f"{FROZEN} line {number}: {path} is not relative to the repo root")
            continue
        pins.append((match.group(1), path))
    return tuple(pins), tuple(problems)


def frozen_problems(root: Path, tagged: str | None) -> tuple[str, ...]:
    """Every way the eval files break the freeze rule; empty means the check passes.

    Passes when neither `questions.toml` nor `FROZEN` exists (and no answers or
    amendments either), or when `FROZEN` lists `questions.toml` and every file
    it lists exists with its listed hash. `tagged` is `FROZEN` at the
    `eval-frozen` tag, or None while the tag does not exist.
    """
    frozen = root / FROZEN
    if not frozen.exists():
        return tuple(
            f"{path} exists without {FROZEN}"
            for path in (QUESTIONS, ANSWERS, AMENDMENTS)
            if (root / path).exists()
        )
    text = frozen.read_text(encoding="utf-8")
    pins, problems = parse_frozen(text)
    found = [path for _, path in pins]
    if QUESTIONS not in found:
        problems += (f"{FROZEN} does not list {QUESTIONS}",)
    for path in sorted({path for path in found if found.count(path) > 1}):
        problems += (f"{FROZEN} lists {path} more than once",)
    for expected, path in pins:
        if not (root / path).is_file():
            problems += (f"{path} is listed in {FROZEN} but missing",)
        elif sha256_file(root / path) != expected:
            problems += (f"{path} differs from its hash in {FROZEN}",)
    if tagged is not None and tagged != text:
        problems += (f"{FROZEN} differs from its copy at the {TAG} tag",)
    return problems


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    except OSError as error:
        raise EvalError(f"git could not run: {error.strerror}") from None


def tagged_frozen(root: Path) -> str | None:
    """`FROZEN` as committed at the `eval-frozen` tag, or None while the tag does not resolve.

    A tag that resolves without the file gives an empty text, which then differs.
    """
    if _git(root, "rev-parse", "--quiet", "--verify", f"refs/tags/{TAG}").returncode != 0:
        return None
    shown = _git(root, "show", f"refs/tags/{TAG}:{FROZEN}")
    return shown.stdout if shown.returncode == 0 else ""
