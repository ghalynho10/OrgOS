"""The conclusion's markdown, read into its title, six sections and caveat (spec 0016).

Pure: it parses `site/conclusion.md` with `markdown-it-py` (CommonMark with the
table rule on, raw HTML off) and renders each section unchanged, adding only
markup: an anchor on each heading 2, `scope="col"` on each header cell, and a
named, focusable scroll box around each table (AC-22, AC-37). The caveat
sentences are taken from the text, never typed (AC-10, AC-34).
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from markdown_it import MarkdownIt
from markdown_it.token import Token

from orgos.site import SiteError

SECTIONS: Final = 6
CAVEAT_START: Final = "For none of the three question types"
CAVEAT_SECTION: Final = 0
SENTENCE_END: Final = re.compile(r"\.(?: |$)")
NOT_ANCHOR: Final = re.compile(r"[^0-9a-z]+")


@dataclass(frozen=True)
class Section:
    """One heading 2 of the conclusion and the HTML of everything under it."""

    heading: str
    heading_html: str
    anchor: str
    body_html: str


@dataclass(frozen=True)
class Conclusion:
    """The conclusion as the page shows it."""

    title: str
    title_html: str
    sections: tuple[Section, ...]
    caveat: str


def _markdown() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False}).enable("table")


def anchor(text: str) -> str:
    """The heading text lower cased, each run of other characters one `-`, trimmed."""
    return NOT_ANCHOR.sub("-", text.lower()).strip("-")


def plain_text(inline: Token) -> str:
    """The text of an inline token with its markdown marks removed."""
    parts = []
    for child in inline.children or ():
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


def caveat_sentences(paragraph: str) -> str | None:
    """The first two sentences; a sentence ends at a full stop followed by a space (AC-10)."""
    ends = [match.start() for match in SENTENCE_END.finditer(paragraph)]
    if len(ends) < 2:
        return None
    return paragraph[: ends[1] + 1]


def _render_body(md: MarkdownIt, tokens: Sequence[Token], heading: str) -> str:
    """Render a section's blocks unchanged, with each table in a named scroll box."""
    parts = []
    label = heading
    start = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "inline" and index and tokens[index - 1].type == "heading_open":
            label = plain_text(token)
        if token.type != "table_open":
            index += 1
            continue
        close = next(i for i in range(index, len(tokens)) if tokens[i].type == "table_close")
        parts.append(md.renderer.render(list(tokens[start:index]), md.options, {}))
        table = list(tokens[index : close + 1])
        table[0].attrSet("aria-label", label)
        for cell in table:
            if cell.type == "th_open":
                cell.attrSet("scope", "col")
        name = html.escape(label)
        parts.append(f'<div class="scroll" tabindex="0" role="region" aria-label="{name}">\n')
        parts.append(md.renderer.render(table, md.options, {}))
        parts.append("</div>\n")
        start = index = close + 1
    parts.append(md.renderer.render(list(tokens[start:]), md.options, {}))
    return "".join(parts)


def _first_paragraph(tokens: Sequence[Token], starting: str = "") -> str | None:
    for index, token in enumerate(tokens):
        if token.type == "paragraph_open":
            text = plain_text(tokens[index + 1])
            if text.startswith(starting):
                return text
    return None


def conclusion(text: str) -> Conclusion:
    """Read the conclusion: one heading 1, then exactly six heading 2 sections."""
    md = _markdown()
    tokens = md.parse(text)
    if not tokens or tokens[0].type != "heading_open" or tokens[0].tag != "h1":
        raise SiteError("the conclusion does not start with a heading 1")
    title_inline = tokens[1]
    starts = [
        index
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2"
    ]
    if len(starts) != SECTIONS:
        raise SiteError(f"the conclusion has {len(starts)} heading 2 sections, not {SECTIONS}")
    if starts[0] != 3:
        raise SiteError("the conclusion has text between its title and its first section")
    if any(token.tag == "h1" for token in tokens[3:] if token.type == "heading_open"):
        raise SiteError("the conclusion has a second heading 1")
    sections = []
    bodies = []
    for number, start in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(tokens)
        heading = plain_text(tokens[start + 1])
        body = tokens[start + 3 : end]
        bodies.append(body)
        sections.append(
            Section(
                heading=heading,
                heading_html=md.renderer.render(tokens[start + 1].children or [], md.options, {}),
                anchor=anchor(heading),
                body_html=_render_body(md, body, heading),
            )
        )
    paragraph = _first_paragraph(bodies[CAVEAT_SECTION], CAVEAT_START)
    caveat = caveat_sentences(paragraph) if paragraph is not None else None
    if caveat is None:
        raise SiteError(
            f"section 1 of the conclusion has no paragraph that begins {CAVEAT_START!r}"
            " with two sentences (AC-34)"
        )
    return Conclusion(
        title=plain_text(title_inline),
        title_html=md.renderer.render(title_inline.children or [], md.options, {}),
        sections=tuple(sections),
        caveat=caveat,
    )
