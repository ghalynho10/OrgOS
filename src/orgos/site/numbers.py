"""The numbers check: every number on the page has a source (spec 0016 AC-9).

Pure. It collects the number tokens of the page's visible text (text nodes
outside `script` and `style`, never attributes) and returns those that are not
tokens of the report fields the page reads, the conclusion or the constants.
It catches a number from nowhere, not a right number in the wrong cell; the
regeneration check carries that.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Final

NUMBER: Final = re.compile(r"(?<![A-Za-z0-9])\d+(?:[./]\d+)?(?![A-Za-z0-9])")
HIDDEN: Final = frozenset({"script", "style"})


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in HIDDEN:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.chunks.append(data)


def visible_text(page: str) -> tuple[str, ...]:
    """The page's text nodes outside `script` and `style`, one per node."""
    parser = _VisibleText()
    parser.feed(page)
    parser.close()
    return tuple(parser.chunks)


def tokens(texts: Iterable[str]) -> frozenset[str]:
    """The number tokens of each text, read separately so no two nodes join."""
    return frozenset(token for chunk in texts for token in NUMBER.findall(chunk))


def unsourced(page: str, sources: Iterable[str]) -> tuple[str, ...]:
    """The page's number tokens that no source holds, sorted."""
    return tuple(sorted(tokens(visible_text(page)) - tokens(sources)))


def page_tokens(page: str) -> frozenset[str]:
    """The number tokens of the page's visible text."""
    return tokens(visible_text(page))
