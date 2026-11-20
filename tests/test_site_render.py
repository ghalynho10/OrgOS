"""The page from the real conclusion and report: order, cells, caveats, markup (spec 0016).

The expected cells are read from the report here, by the paths the spec's
*Value sourcing* names, never through `orgos.site.evidence`.
"""

import itertools
import re
from pathlib import Path
from typing import Any

from orgos.site import text
from orgos.site.build import build
from orgos.site.render import inline_code
from orgos.site.style import STYLESHEET
from tests.site_pages import (
    conclusion_text,
    copy_inputs,
    page,
    report,
    report_copy,
    tables,
    tree,
)

TYPES = ("aggregate", "path_connection", "mixed")
STORES = ("sql", "graph")
CAVEAT = (
    "For none of the three question types could the test tell whether graph or SQL did better."
    " It doesn't show they're the same either."
)
GENERATED = ("table-rule", "table-counts", "table-zero", "table-differences", "table-note")


def _c(node: dict[str, Any], right: str = "answer_match") -> str:
    return f"{node[right]}/{node['total']}"


def _beside(made: Any, kind: str, store: str) -> Any:
    return next(e for e in made["plain"]["noted_beside_plain"][kind] if e["store"] == store)


def _rule(made: Any, kind: str) -> Any:
    return next(
        t
        for t in made["tests"]["full form"]
        if t["role"] == "primary" and t["name"] == f"noted, full form, {kind}"
    )


def _sections(html: str) -> list[str]:
    return [s.text() for s in tree(html).all("section")]


def _strip_marks(markdown: str) -> list[str]:
    words = []
    for line in markdown.splitlines():
        if re.fullmatch(r"\s*\|[\s|:-]+\|\s*", line):
            continue
        if line.startswith("#"):
            line = re.sub(r"^#+\s+", "", line)
        else:
            line = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", line)
        line = line.replace("|", " ").replace("`", "").replace("**", "")
        words += line.split()
    return words


def _conclusion_sections() -> list[str]:
    return re.split(r"(?m)^(?=## )", conclusion_text())[1:]


# covers: AC-2
def test_sections_word_for_word() -> None:
    html = page()
    found = tree(html).all("section")[:6]
    assert len(found) == 6
    for section, source in zip(found, _conclusion_sections(), strict=True):
        words = []
        for child in section.children:
            if not isinstance(child, str) and (
                child.attrs.get("aria-labelledby", "").startswith("table-")
                or child.attrs.get("class") == "note"
            ):
                break
            words += (child if isinstance(child, str) else child.text()).split()
        assert words == _strip_marks(source)


# covers: AC-3
def test_page_order() -> None:
    html = page()
    title = "Graph or SQL, and when? Conclusion of Phase 1"
    assert 'class="dek"' not in html
    marks = [
        html.index(f"<p>{text.K1_HEADER}</p>"),
        html.index(f"<h1>{title}</h1>"),
        html.index('<p class="byline">'),
        html.index(f'<p class="note">{text.K7_NOT_CLAIMED}'),
    ]
    sections = tree(html).all("section")
    for number, heading in enumerate(re.findall(r"(?m)^## (.*)$", conclusion_text())):
        marks.append(html.index(f">{heading}</h2>"))
        for key in (GENERATED[:1], GENERATED[1:3], GENERATED[3:4], GENERATED[4:], (), ())[number]:
            marks.append(html.index(f'id="{key}"'))
        if number == 4:
            marks.append(html.index(f'<a href="{text.K12_URL}">'))
            last = [child for child in sections[4].children if not isinstance(child, str)][-1]
            assert last.text().startswith(text.K12_BEFORE)
    marks += [
        html.index(text.K8_PRIVATE_SPECS),
        html.index('<footer class="band band-bottom">'),
        html.index(text.K9_CREDIT.replace("Lüders", "L")[:30]),
        html.index("The public code repository also holds"),
    ]
    assert marks == sorted(marks)
    assert html.index("</main>") < html.index("<footer")
    assert len(sections) == 6
    assert "Limitations</h2>" not in html


# covers: AC-4
def test_cells_from_report() -> None:
    made = report()
    found = tables(page())
    assert set(found) == set(GENERATED)
    assert found["table-rule"]["rows"] == [
        [
            kind,
            str(_rule(made, kind)["n"]),
            str(_rule(made, kind)["m"]),
            str(_rule(made, kind)["graph_ahead"]),
            str(_rule(made, kind)["sql_ahead"]),
            _rule(made, kind)["p"],
            _rule(made, kind)["smallest_p"],
        ]
        for kind in TYPES
    ]
    counts = []
    zero = []
    for kind in TYPES:
        for store in STORES:
            agent = made["tables"]["full form"][store][kind]["agent"]
            beside = _beside(made, kind, store)
            counts.append(
                [
                    kind,
                    text.L6_STORES[store],
                    _c(agent, "correct"),
                    _c(beside["full"]["plain"]),
                    str(agent["runs_with_a_retry"]),
                    str(agent["attempts_timed_out"]),
                    str(agent["attempts_at_row_cap"]),
                    str(agent["limit_hit"]["requests"]),
                    str(agent["limit_hit"]["tokens"]),
                    str(agent["limit_hit"]["tool_calls"]),
                ]
            )
            zero.append(
                [
                    kind,
                    text.L6_STORES[store],
                    _c(beside["full"]["noted"]),
                    _c(beside["full"]["plain"]),
                    _c(beside["without_zero_guarded"]["noted"]),
                    _c(beside["without_zero_guarded"]["plain"]),
                ]
            )
    assert found["table-counts"]["rows"] == counts
    assert found["table-zero"]["rows"] == zero
    assert found["table-zero"]["caption"].endswith(" and ".join(made["zero_answer_questions"]))
    differences = [
        [question, kind, f"+{d}" if d > 0 else (f"\N{MINUS SIGN}{-d}" if d < 0 else "0")]
        for kind in TYPES
        for question, d in zip(
            _beside(made, kind, "sql")["compared"], _rule(made, kind)["differences"], strict=True
        )
    ]
    assert found["table-differences"]["rows"] == differences
    assert len(differences) == 20
    notes = [
        [
            "All questions",
            text.L6_STORES[s],
            _c(made["plain"]["drop"][s]["noted"]),
            _c(made["plain"]["drop"][s]["plain"]),
        ]
        for s in STORES
    ] + [
        ["path_connection", text.L6_STORES[s]]
        + [_c(_beside(made, "path_connection", s)["full"][k]) for k in ("noted", "plain")]
        for s in STORES
    ]
    assert found["table-note"]["rows"] == notes


def _swapped() -> Any:
    made = report_copy()
    full = made["tables"]["full form"]
    full["sql"], full["graph"] = full["graph"], full["sql"]
    for entries in made["plain"]["noted_beside_plain"].values():
        for entry in entries:
            entry["store"] = {"sql": "graph", "graph": "sql"}[entry["store"]]
    drop = made["plain"]["drop"]
    drop["sql"], drop["graph"] = drop["graph"], drop["sql"]
    for rule in made["tests"]["full form"]:
        rule["differences"] = [-d for d in rule["differences"]]
        rule["graph_ahead"], rule["sql_ahead"] = rule["sql_ahead"], rule["graph_ahead"]
    return made


# covers: AC-30
def test_fixed_order() -> None:
    for html in (page(), page(made=_swapped())):
        found = tables(html)
        assert [r[0] for r in found["table-rule"]["rows"]] == list(TYPES)
        for key in ("table-counts", "table-zero"):
            assert [r[:2] for r in found[key]["rows"]] == [
                [kind, text.L6_STORES[s]] for kind in TYPES for s in STORES
            ]
        assert [r[:2] for r in found["table-note"]["rows"]] == [
            [scope, text.L6_STORES[s]] for scope in text.L5_SCOPES for s in STORES
        ]
        assert [r[1] for r in found["table-differences"]["rows"]] == [
            *["aggregate"] * 7,
            *["path_connection"] * 7,
            *["mixed"] * 6,
        ]
        assert found["table-rule"]["headers"][3:5] == ["Graph ahead", "SQL ahead"]
        assert found["table-differences"]["headers"][2] == "Graph minus SQL"
    assert (
        tables(page())["table-counts"]["rows"]
        != tables(page(made=_swapped()))["table-counts"]["rows"]
    )


# covers: AC-11
def test_no_emphasis() -> None:
    def masked(html: str) -> str:
        return re.sub(r"<td>[^<]*</td>", "<td></td>", html)

    original, swapped = page(), page(made=_swapped())
    assert masked(original) == masked(swapped)
    for html in (original, swapped):
        for row in re.findall(r"<tbody>.*?</tbody>", html, re.S):
            assert not re.search(r"<(?:strong|b|em|mark)\b|class=|style=", row)


# covers: AC-10
def test_caveat_under_every_comparison_table() -> None:
    html = page()
    assert CAVEAT in conclusion_text()
    for key in GENERATED:
        after = html[html.index(f'id="{key}"') :]
        after = after[after.index("</div>") :]
        assert after.startswith(f'</div>\n<p class="caveat">{CAVEAT.replace("'", "&#x27;")}</p>')


# covers: AC-33
def test_g2_6_then_k11_under_the_reading_note_table_only() -> None:
    html = page()
    line = next(item for item in report()["limitations"] if item["id"] == "G2-6")
    section = tree(html).all("section")[3]
    paragraphs = [p.text() for p in section.all("p")]
    assert paragraphs[-2] == f"G2-6 {line['text']} {line['source']}"
    assert paragraphs[-1] == text.K11_UNTESTED
    assert html.count(text.K11_UNTESTED) == 1
    assert html.count('<span class="lim-id">G2-6</span>') == 1


# covers: AC-12
def test_no_limitation_list() -> None:
    html = page()
    shown = [s.text() for s in tree(html).all("span") if s.attrs.get("class") == "lim-id"]
    assert shown == ["G2-6"]
    others = [line for line in report()["limitations"] if line["id"] != "G2-6"]
    assert len(others) > 0
    visible = tree(html).text()
    assert not any(line["text"].replace("`", "") in visible for line in others)
    assert "Limitations</h2>" not in html
    contents = [a.text() for a in tree(html).find("nav").all("a")]
    assert contents == re.findall(r"(?m)^## (.*)$", conclusion_text())


# covers: AC-48
def test_h3_only_from_the_conclusion() -> None:
    root = tree(page())
    headings = root.all("h3")
    assert [h.text() for h in headings] == re.findall(r"(?m)^### (.*)$", conclusion_text())
    assert headings
    assert all("id" not in h.attrs for h in headings)
    contents = [a.text() for a in root.find("nav").all("a")]
    assert contents == re.findall(r"(?m)^## (.*)$", conclusion_text())


def _report_links(html: str) -> list[str]:
    return [a.attrs.get("href", "") for a in tree(html).all("a") if a.text() == text.K12_LINK_TEXT]


# covers: AC-44
def test_report_link(tmp_path: Path) -> None:
    html = page()
    assert _report_links(html) == [
        "https://github.com/ghalynho10/OrgOS/blob/main/results/findings/report.json"
    ]
    assert html.count(text.K12_URL) == 1
    without = html.replace(f'<a href="{text.K12_URL}">', "<a>")
    assert _report_links(without) != [text.K12_URL]
    root = copy_inputs(tmp_path)
    build(root, accept=False)
    assert (root / "results/findings/report.json").is_file()
    assert _report_links((root / "site/index.html").read_text(encoding="utf-8")) == [text.K12_URL]


def _with_g2_6(line_text: str) -> str:
    made = report_copy()
    line = next(item for item in made["limitations"] if item["id"] == "G2-6")
    line["text"] = line_text
    return page(made=made)


# covers: AC-45
def test_inline_code() -> None:
    assert inline_code("a `b` c") == "a <code>b</code> c"
    assert inline_code("a `b") == "a `b"
    assert inline_code("`x` and `y` then `z") == "<code>x</code> and <code>y</code> then `z"
    assert inline_code("<a> & `<b>`") == "&lt;a&gt; &amp; <code>&lt;b&gt;</code>"
    paired = tree(_with_g2_6("see `orgos eval report` here")).all("p")
    line = next(p for p in paired if p.attrs.get("class") == "limitation")
    codes = line.all("code")
    assert [c.text() for c in codes] == ["orgos eval report"]
    single = tree(_with_g2_6("one ` backtick")).all("p")
    line = next(p for p in single if p.attrs.get("class") == "limitation")
    assert line.all("code") == []
    assert "one ` backtick" in line.text()


# covers: AC-14
def test_no_load_id_hash_or_timestamp() -> None:
    html = page()
    made = report()
    assert made["load_id"] not in html
    assert not re.search(r"(?i)load id|sha256|generated from|\b[0-9a-f]{12,}\b", html)
    assert not re.search(r"\d{4}-\d{2}-\d{2}T|\d{2}:\d{2}:\d{2}", html)


# covers: AC-19
def test_unknown_keys_do_not_change_the_page() -> None:
    made = report_copy()
    made["an_unknown_key"] = {"value": 12345}
    made["tests"]["an unknown form"] = [{"name": "noted, full form, aggregate", "n": 99}]
    assert page(made=made) == page()


# covers: AC-22
def test_table_markup() -> None:
    root = tree(page())
    for th in root.all("th"):
        assert th.attrs.get("scope") == "col"
    for table in root.all("table"):
        captions = table.all("caption")
        if captions:
            continue
        assert table.attrs["aria-label"] == "1. What the frozen rule said"
    assert sum(1 for t in root.all("table") if t.all("caption")) == 5


# covers: AC-22
def test_structure() -> None:
    html = page()
    assert '<html lang="en">' in html
    levels = [int(h[1]) for h in re.findall(r"<(h[1-6])\b", html)]
    assert levels.count(1) == 1 and levels[0] == 1
    assert all(b - a <= 1 for a, b in itertools.pairwise(levels))


# covers: AC-37
def test_scroll_boxes_are_focusable_and_named() -> None:
    root = tree(page())
    ids = {node.attrs["id"]: node.text() for node in root.all() if "id" in node.attrs}
    boxes = [d for d in root.all("div") if "scroll" in d.attrs.get("class", "").split()]
    assert len(boxes) == 6
    for box in boxes:
        assert box.attrs["tabindex"] == "0"
        named = box.attrs.get("aria-label") or ids.get(box.attrs.get("aria-labelledby", ""))
        assert named
    assert re.search(r":focus-visible \{ outline: 2px solid", STYLESHEET)


# covers: AC-23
def test_head() -> None:
    html = page()
    head = html[: html.index("</head>")]
    title = "Graph or SQL, and when? Conclusion of Phase 1"
    assert f"<title>{title} | OrgOS</title>" in head
    root = tree(head)
    metas = {m.attrs.get("name") or m.attrs.get("property"): m.attrs for m in root.all("meta")}
    assert metas["description"]["content"] == text.K7_NOT_CLAIMED
    assert metas["og:description"]["content"] == text.K7_NOT_CLAIMED
    assert metas["og:title"]["content"] == title
    assert '<link rel="canonical" href="https://ghalynho10.github.io/OrgOS/">' in head


# covers: AC-39
def test_no_script_and_no_other_host() -> None:
    html = page()
    assert "<script" not in html
    assert "cookie" not in html.lower()
    urls = re.findall(r'(?:href|src|content)="(https?://[^"]+)"', html)
    assert sorted(urls) == sorted(
        [
            text.CANONICAL_URL,
            text.CANONICAL_URL,
            text.K2_AUTHOR_URL,
            text.K2_CODE_URL,
            text.K12_URL,
        ]
    )
    assert "url(" not in STYLESHEET and "@import" not in STYLESHEET
