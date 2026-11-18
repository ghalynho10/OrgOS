"""`orgos site build` and `orgos site check`: the only file reads and writes of the page.

`build` copies the conclusion into `site/` (only with `--accept` when it changed)
and writes the page and its stylesheet. `check` regenerates both in memory from
the tracked copy and the report and compares them byte for byte, checks every
number on the page has a source, and scans every file under `site/` for email
addresses and the name list (spec 0016). Neither opens a store, calls a model or
loads `Settings`, and neither opens `eval/answers.json`, `results/agent_runs/` or
`gold/` (AC-18, AC-29).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from orgos.site import SiteError, text
from orgos.site.conclusion import conclusion
from orgos.site.evidence import evidence
from orgos.site.numbers import page_tokens, unsourced
from orgos.site.render import page
from orgos.site.scan import email_matches, name_list, name_matches
from orgos.site.style import STYLESHEET

CONCLUSION_SOURCE: Final = Path("docs/findings/conclusion.md")
REPORT: Final = Path("results/findings/report.json")
SITE: Final = Path("site")
CONCLUSION_COPY: Final = SITE / "conclusion.md"
PAGE: Final = SITE / "index.html"
STYLE: Final = SITE / "style.css"
FIXTURES: Final = Path("tests/fixtures/corpus")
DESCRIPTION: Final = Path("eval/data-description.md")


def render_site(conclusion_text: str, report: Any) -> tuple[str, str]:
    """The page and its stylesheet from the conclusion's text and the parsed report."""
    read = conclusion(conclusion_text)
    shown = evidence(report)
    return page(read, shown), STYLESHEET


def sources(conclusion_text: str, report: Any) -> tuple[str, ...]:
    """Every text a number on the page may come from (AC-9)."""
    return (*evidence(report).texts(), conclusion_text, *text.ALL_TEXT)


def _read_report(root: Path) -> Any:
    path = root / REPORT
    if not path.is_file():
        raise SiteError(f"{REPORT} is missing; run `orgos eval report` first")
    return json.loads(path.read_text(encoding="utf-8"))


def _read(path: Path, shown: Path) -> bytes:
    if not path.is_file():
        raise SiteError(f"{shown} is missing")
    return path.read_bytes()


def build(root: Path, *, accept: bool) -> tuple[str, ...]:
    """Write `site/index.html` and `site/style.css`, and the conclusion copy with `--accept`.

    Refuses, printing no text of the conclusion, when the conclusion differs from
    the tracked copy or no copy exists yet and `--accept` is not given (AC-7).
    Everything is built in memory first, so a refusal writes nothing.
    """
    source = _read(root / CONCLUSION_SOURCE, CONCLUSION_SOURCE)
    copy_path = root / CONCLUSION_COPY
    copy = copy_path.read_bytes() if copy_path.is_file() else None
    lines = []
    if copy != source:
        if not accept:
            reason = "no copy exists yet" if copy is None else "it differs from the copy"
            raise SiteError(
                f"{CONCLUSION_SOURCE} is not accepted ({reason}); read the difference, then run"
                " `orgos site build --accept`"
            )
        lines.append(f"accepted {CONCLUSION_SOURCE} into {CONCLUSION_COPY}")
    page_text, style_text = render_site(source.decode("utf-8"), _read_report(root))
    (root / SITE).mkdir(parents=True, exist_ok=True)
    if copy != source:
        copy_path.write_bytes(source)
    (root / PAGE).write_bytes(page_text.encode("utf-8"))
    (root / STYLE).write_bytes(style_text.encode("utf-8"))
    return (*lines, f"wrote {PAGE} and {STYLE}")


def _fixture_documents(root: Path) -> tuple[Any, ...]:
    return tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / FIXTURES).glob("*.json"))
    )


def check(root: Path) -> tuple[str, ...]:
    """Regenerate, compare, check the numbers and scan; raise `SiteError` listing every failure."""
    conclusion_text = _read(root / CONCLUSION_COPY, CONCLUSION_COPY).decode("utf-8")
    report = _read_report(root)
    lines = []
    problems = []
    page_text, style_text = render_site(conclusion_text, report)
    committed_page = _read(root / PAGE, PAGE)
    for path, made in ((PAGE, page_text), (STYLE, style_text)):
        if _read(root / path, path) != made.encode("utf-8"):
            problems.append(f"{path} differs from a regeneration; run `orgos site build`")
    if not problems:
        lines.append(f"regeneration: {PAGE} and {STYLE} equal the build from {CONCLUSION_COPY}")
    published = committed_page.decode("utf-8")
    unknown = unsourced(published, sources(conclusion_text, report))
    if unknown:
        problems.append(f"numbers with no source on the page: {', '.join(unknown)}")
    else:
        lines.append(f"numbers: {len(page_tokens(published))} distinct number tokens, all sourced")
    files = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / SITE).rglob("*"))
        if path.is_file()
    }
    lines.append(f"scan: {len(files)} files under {SITE}/: {', '.join(files)}")
    emails = email_matches(files)
    lines.append(f"emails: {len(emails)} matches")
    problems += [f"email address in {match.file}, line {match.line}" for match in emails]
    try:
        names = name_list(
            _fixture_documents(root), _read(root / DESCRIPTION, DESCRIPTION).decode("utf-8")
        )
    except SiteError as error:
        problems.append(str(error))
    else:
        counts = ", ".join(
            f"source {number}: {count}" for number, count in enumerate(names.source_counts, 1)
        )
        lines.append(f"name list: {len(names.entries)} entries ({counts})")
        found = name_matches(files, names)
        lines.append(f"names: {len(found)} matches")
        problems += [
            f"list entry {match.entry} in {match.file}, line {match.line}" for match in found
        ]
    if problems:
        raise SiteError("\n".join(["site check failed:", *problems, "", *lines]))
    return (*lines, "site check passed")
