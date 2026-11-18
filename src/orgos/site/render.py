"""The page: the conclusion's six sections, each followed by its evidence (spec 0016).

Pure: it joins the rendered conclusion, the report's rows and the *Page text*
constants into one HTML string, in the order of AC-3. Every table keeps the
fixed order of AC-30, no cell or row is marked (AC-11), and the caveat sits
under each comparison table (AC-10). The page has no script and makes no
request to another host (AC-39).
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from typing import Final

from orgos.site import text
from orgos.site.conclusion import Conclusion, Section, anchor
from orgos.site.evidence import NOTE_LIMITATION, Count, Evidence, Limitation

# The conclusion's own minus sign, U+2212, never a hyphen.
MINUS: Final = "\N{MINUS SIGN}"


def _signed(value: int) -> str:
    if value > 0:
        return f"+{value}"
    if value < 0:
        return f"{MINUS}{-value}"
    return "0"


def _store(store: str) -> str:
    return text.L6_STORES[store]


def _table(
    key: str, caption: str, headers: Sequence[str], rows: Sequence[Sequence[str | Count]]
) -> str:
    """One generated table in a focusable scroll box named by its caption."""
    caption_id = f"table-{key}"
    head = "".join(f'<th scope="col">{escape(header)}</th>' for header in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(cell.text if isinstance(cell, Count) else cell)}</td>" for cell in row
        )
        + "</tr>\n"
        for row in rows
    )
    return (
        f'<div class="scroll" tabindex="0" role="region" aria-labelledby="{caption_id}">\n'
        f'<table>\n<caption id="{caption_id}">{escape(caption)}</caption>\n'
        f"<thead>\n<tr>{head}</tr>\n</thead>\n<tbody>\n{body}</tbody>\n</table>\n</div>\n"
    )


def _caveat(caveat: str) -> str:
    return f'<p class="caveat">{escape(caveat)}</p>\n'


def _limitation(line: Limitation, tag: str, css: str) -> str:
    return (
        f'<{tag} class="{css}"><span class="lim-id">{escape(line.id)}</span> '
        f'<span class="lim-text">{escape(line.text)}</span> '
        f'<span class="lim-source">{escape(line.source)}</span></{tag}>\n'
    )


def _questions(evidence: Evidence) -> str:
    return " and ".join(evidence.zero_questions)


def _section_1(evidence: Evidence, caveat: str) -> str:
    rows = [
        (
            rule.type,
            str(rule.n),
            str(rule.m),
            str(rule.graph_ahead),
            str(rule.sql_ahead),
            rule.p,
            rule.smallest_p,
        )
        for rule in evidence.rules
    ]
    return _table("rule", text.K4_RULE, text.L1_RULE, rows) + _caveat(caveat)


def _section_2(evidence: Evidence, caveat: str) -> str:
    counts = [
        (
            row.type,
            _store(row.store),
            row.correct,
            row.plain,
            str(row.runs_with_a_retry),
            str(row.attempts_timed_out),
            str(row.attempts_at_row_cap),
            str(row.limit_requests),
            str(row.limit_tokens),
            str(row.limit_tool_calls),
        )
        for row in evidence.counts
    ]
    zero = [
        (row.type, _store(row.store), row.noted, row.plain, row.noted_without, row.plain_without)
        for row in evidence.zero
    ]
    questions = _questions(evidence)
    return (
        _table("counts", text.K4_COUNTS, text.L2_COUNTS, counts)
        + _caveat(caveat)
        + _table(
            "zero",
            text.K4_ZERO.format(questions=questions),
            [header.format(questions=questions) for header in text.L3_ZERO],
            zero,
        )
        + _caveat(caveat)
    )


def _section_3(evidence: Evidence, caveat: str) -> str:
    rows = [(row.question, row.type, _signed(row.difference)) for row in evidence.differences]
    return _table("differences", text.K4_DIFFERENCES, text.L4_DIFFERENCES, rows) + _caveat(caveat)


def _section_4(evidence: Evidence, caveat: str) -> str:
    scopes = {"all": text.L5_SCOPES[0], "path_connection": text.L5_SCOPES[1]}
    rows = [(scopes[row.scope], _store(row.store), row.noted, row.plain) for row in evidence.notes]
    note = next(line for line in evidence.limitations if line.id == NOTE_LIMITATION)
    return (
        _table("note", text.K4_NOTE, text.L5_NOTE, rows)
        + _caveat(caveat)
        + _limitation(note, "p", "limitation")
        + _caveat(text.K11_UNTESTED)
    )


EVIDENCE: Final = (_section_1, _section_2, _section_3, _section_4)


def _section(section: Section, evidence: str) -> str:
    return (
        f'<section>\n<h2 id="{section.anchor}">{section.heading_html}</h2>\n'
        f"{section.body_html}{evidence}</section>\n"
    )


def _limitations(evidence: Evidence) -> str:
    groups = []
    for number, heading in enumerate(text.K5_GROUPS, start=1):
        lines = "".join(
            _limitation(line, "li", "limitation")
            for line in evidence.limitations
            if line.group == number
        )
        groups.append(f'<h3>{escape(heading)}</h3>\n<ul class="limitations">\n{lines}</ul>\n')
    heading = text.K6_LIMITATIONS
    return (
        f'<section>\n<h2 id="{anchor(heading)}">{escape(heading)}</h2>\n'
        + "".join(groups)
        + "</section>\n"
    )


def _contents(conclusion: Conclusion) -> str:
    entries = [(section.anchor, escape(section.heading)) for section in conclusion.sections]
    entries.append((anchor(text.K6_LIMITATIONS), escape(text.K6_LIMITATIONS)))
    items = "".join(f'<li><a href="#{key}">{label}</a></li>\n' for key, label in entries)
    return (
        '<nav class="toc" aria-labelledby="toc-label">\n'
        f'<p id="toc-label" class="toc-label">{escape(text.L7_CONTENTS)}</p>\n'
        f"<ul>\n{items}</ul>\n</nav>\n"
    )


def _head(conclusion: Conclusion) -> str:
    title = escape(conclusion.title)
    dek = escape(conclusion.dek)
    url = escape(text.CANONICAL_URL)
    return (
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title} | {escape(text.K1_TITLE_WORD)}</title>\n"
        f'<meta name="description" content="{dek}">\n'
        f'<link rel="canonical" href="{url}">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{dek}">\n'
        f'<meta property="og:url" content="{url}">\n'
        '<link rel="stylesheet" href="style.css">\n'
        "</head>\n"
    )


def page(conclusion: Conclusion, evidence: Evidence) -> str:
    """The whole page, in the order of AC-3."""
    sections = "".join(
        _section(
            section,
            EVIDENCE[number](evidence, conclusion.caveat) if number < len(EVIDENCE) else "",
        )
        for number, section in enumerate(conclusion.sections)
    )
    byline = (
        f'<p class="byline"><a href="{escape(text.K2_AUTHOR_URL)}">{escape(text.K2_AUTHOR)}</a>'
        f'<br>\n<a href="{escape(text.K2_CODE_URL)}">{escape(text.K2_CODE)}</a></p>\n'
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        + _head(conclusion)
        + "<body>\n"
        + '<header class="band band-top"><div class="band-inner">'
        + f"<p>{escape(text.K1_HEADER)}</p></div></header>\n"
        + '<div class="layout">\n'
        + _contents(conclusion)
        + '<main class="column">\n'
        + f"<h1>{conclusion.title_html}</h1>\n"
        + byline
        + f'<p class="dek">{escape(conclusion.dek)}</p>\n'
        + sections
        + _limitations(evidence)
        + f'<p class="note">{escape(text.K7_NOT_CLAIMED)}</p>\n'
        + f'<p class="note">{escape(text.K8_PRIVATE_SPECS)}</p>\n'
        + "</main>\n</div>\n"
        + '<footer class="band band-bottom"><div class="band-inner">\n'
        + f"<p>{escape(text.K9_CREDIT)}</p>\n"
        + f"<p>{escape(text.K10_ALREADY_PUBLISHED)}</p>\n"
        + "</div></footer>\n"
        + "</body>\n</html>\n"
    )
