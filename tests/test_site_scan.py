"""The scan for emails and list names, on made up fixtures (spec 0016 AC-15 to AC-18, AC-36).

The fixtures here are invented, in the shape of the corpus fixtures and the data
description, so no real name from the dataset is written in a test.
"""

import builtins
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from orgos.site import SiteError
from orgos.site.build import build, check
from orgos.site.scan import (
    ALLOWLIST,
    NameList,
    email_matches,
    entry_hash,
    name_list,
    name_matches,
)
from tests.site_pages import copy_inputs

USER = {
    "displayName": "Zelda Quartermaine",
    "name": "zquarter",
    "key": "zq",
    "emailAddress": "zelda.q@example.org",
}


def _issue() -> dict[str, Any]:
    return {
        "key": "DEMO-1",
        "fields": {
            "creator": USER,
            "comments": [{"author": {"name": "orlagh.b"}, "body": "ask wendeline@example.net"}],
        },
        "changelog": {
            "histories": [
                {
                    "author": {"displayName": "Perpetua Vane"},
                    "items": [
                        {"field": "Assignee", "fromString": "Ivo Marchetti", "toString": None},
                        {"field": "Status", "fromString": "Open", "toString": "Closed"},
                    ],
                }
            ]
        },
    }


DESCRIPTION = (
    '# Data description\n\n### Assignee\n\n- "Ottoline Brack"\n- "Abe"\n\n'
    '### Status\n\n- "Resolved"\n'
)


def _names(description: str = DESCRIPTION) -> NameList:
    return name_list(([_issue()],), description)


# covers: AC-17
def test_the_list_is_sorted_numbered_and_counted_per_source() -> None:
    names = _names()
    assert names.entries == tuple(sorted(names.entries))
    assert names.source_counts == (4, 5, 1, 1)
    assert "ottoline brack" in names.entries and "ivo marchetti" in names.entries
    assert "closed" not in names.entries and "resolved" not in names.entries
    assert "zq" not in names.entries and "abe" not in names.entries


# covers: AC-17
def test_a_source_that_yields_nothing_fails() -> None:
    with pytest.raises(SiteError, match="source 4 yielded no entry"):
        _names(DESCRIPTION.replace("### Assignee", "### Something else"))


# covers: AC-16
def test_names_match_literally_ignoring_case_with_no_word_character_beside() -> None:
    names = _names()
    files = {
        "site/a.html": "line one\n<p>ZELDA QUARTERMAINE wrote</p>\nzquarterly is a longer word\n",
        "site/b.css": "x\nmail zelda.q@example.org\n",
    }
    found = name_matches(files, names)
    number = names.entries.index("zelda quartermaine") + 1
    assert [(m.file, m.line, m.entry) for m in found if m.file == "site/a.html"] == [
        ("site/a.html", 2, number)
    ]
    dotted = names.entries.index("zelda.q@example.org") + 1
    assert ("site/b.css", 2, dotted) in [(m.file, m.line, m.entry) for m in found]


# covers: AC-16
def test_a_match_fails_the_check_printing_no_entry(tmp_path: Path) -> None:
    root = _root(tmp_path)
    page = root / "site/index.html"
    page.write_text(page.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    (root / "site/extra.txt").write_text("first\nby Ivo Marchetti\n", encoding="utf-8")
    with pytest.raises(SiteError) as failed:
        check(root)
    message = str(failed.value)
    assert "list entry" in message and "site/extra.txt, line 2" in message
    assert "marchetti" not in message.lower()


# covers: AC-15
def test_an_email_anywhere_under_site_fails(tmp_path: Path) -> None:
    assert email_matches({"site/x.css": "a\n/* b@c.io */\n"})[0].line == 2
    root = _root(tmp_path)
    (root / "site/notes.md").write_text("write to someone@example.com\n", encoding="utf-8")
    with pytest.raises(SiteError, match=r"email address in site/notes\.md, line 1"):
        check(root)


# covers: AC-36
def test_allowlist_is_empty_and_keyed_by_sha256() -> None:
    assert ALLOWLIST == ()
    names = _names()
    files = {"site/a.html": "Perpetua Vane\n"}
    assert len(name_matches(files, names)) == 1
    allowed = ((entry_hash("perpetua vane"), "a reason"),)
    assert name_matches(files, names, allowed) == ()
    assert entry_hash("perpetua vane") == hashlib.sha256(b"perpetua vane").hexdigest()
    assert _names().entries == names.entries


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    copy_inputs(root)
    corpus = root / "tests/fixtures/corpus"
    for path in corpus.iterdir():
        path.unlink()
    (corpus / "DEMO.json").write_text(json.dumps([_issue()]), encoding="utf-8")
    (root / "eval/data-description.md").write_text(DESCRIPTION, encoding="utf-8")
    return root


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ("tests/fixtures/corpus", "eval")
        for p in sorted((root / folder).rglob("*"))
        if p.is_file()
    }


# covers: AC-18
def test_build_and_check_change_no_fixture_or_eval_file_and_open_no_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    (root / "eval/answers.json").write_text("{}", encoding="utf-8")
    (root / "results/agent_runs").mkdir(parents=True)
    (root / "results/agent_runs/run.json").write_text("{}", encoding="utf-8")
    (root / "gold").mkdir()
    (root / "gold/ACCEPTED").write_text("", encoding="utf-8")
    before = _hashes(root)
    opened: list[str] = []
    real_open = io.open

    def recording_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", recording_open)
    monkeypatch.setattr(builtins, "open", recording_open)
    build(root, accept=True)
    lines = check(root)
    seen = tuple(opened)
    monkeypatch.undo()
    assert _hashes(root) == before
    assert seen
    assert not [p for p in seen if "answers.json" in p or "agent_runs" in p or "/gold" in p]
    assert "names: 0 matches" in lines
    assert "name list: 10 entries (source 1: 4, source 2: 5, source 3: 1, source 4: 1)" in lines
