"""Helpers for the public results page tests (spec 0016): the real inputs and a small HTML tree.

The tests read the tracked `site/conclusion.md` and `results/findings/report.json`,
never `docs/`, so they run in CI where `docs/` is absent.
"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from orgos.site.build import render_site

REPO = Path(__file__).resolve().parents[1]
CONCLUSION = REPO / "site" / "conclusion.md"
REPORT = REPO / "results" / "findings" / "report.json"
VOID = frozenset({"meta", "link", "br", "img", "hr", "input"})


def conclusion_text() -> str:
    return CONCLUSION.read_text(encoding="utf-8")


def report() -> Any:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def report_copy() -> Any:
    return copy.deepcopy(report())


def page(conclusion: str | None = None, made: Any = None) -> str:
    return render_site(conclusion_text() if conclusion is None else conclusion, made or report())[0]


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def all(self, tag: str | None = None) -> list[Node]:
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    found.append(child)
                found += child.all(tag)
        return found

    def find(self, tag: str) -> Node:
        return self.all(tag)[0]


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        while self.stack[-1].tag != tag:
            self.stack.pop()
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def tree(html: str) -> Node:
    parser = _Tree()
    parser.feed(html)
    parser.close()
    return parser.root


def tables(html: str) -> dict[str, dict[str, Any]]:
    """Each generated table by its caption id: caption, headers and rows of cell text."""
    found = {}
    for table in tree(html).all("table"):
        captions = table.all("caption")
        if not captions:
            continue
        found[captions[0].attrs["id"]] = {
            "caption": captions[0].text(),
            "headers": [th.text() for th in table.all("th")],
            "rows": [[td.text() for td in tr.all("td")] for tr in table.find("tbody").all("tr")],
        }
    return found


def copy_inputs(target: Path, *, docs: bool = True) -> Path:
    """Copy what `build` and `check` read into `target`, as a repo root."""
    for relative in ("site/conclusion.md", "site/index.html", "site/style.css"):
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target / relative)
    (target / "results/findings").mkdir(parents=True)
    shutil.copyfile(REPORT, target / "results/findings/report.json")
    shutil.copytree(REPO / "tests/fixtures/corpus", target / "tests/fixtures/corpus")
    (target / "eval").mkdir()
    shutil.copyfile(REPO / "eval/data-description.md", target / "eval/data-description.md")
    if docs:
        (target / "docs/findings").mkdir(parents=True)
        shutil.copyfile(CONCLUSION, target / "docs/findings/conclusion.md")
    return target
