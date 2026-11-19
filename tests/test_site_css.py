"""The stylesheet holds the spec 0016 design tokens (AC-20), and every pair reads (AC-38)."""

import re

from orgos.site import style
from orgos.site.style import STYLESHEET, TEXT_PAIRS


def _luminance(colour: str) -> float:
    channels = []
    for index in (1, 3, 5):
        value = int(colour[index : index + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# covers: AC-20
def test_the_stylesheet_holds_the_layout_tokens() -> None:
    assert ".column { max-width: 645px;" in STYLESHEET
    assert "font-size: 17px;\n  line-height: 1.7;" in STYLESHEET
    assert "text-underline-offset: 2px" in STYLESHEET
    assert "border-bottom: 2px solid #090C0C;" in STYLESHEET
    assert "border-bottom: 1px solid #E3E4E4;" in STYLESHEET
    assert "tbody tr:nth-child(even) { background: #F5F5F5; }" in STYLESHEET
    assert "th:first-child, td:first-child { position: sticky; left: 0;" in STYLESHEET
    assert ".scroll { overflow-x: auto;" in STYLESHEET
    assert 'font-family: -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;' in STYLESHEET
    assert "h1 { font-size: 46px;" in STYLESHEET
    assert "h2 { font-size: 30px; margin: 60px 0 20px; }" in STYLESHEET
    assert "h3 { font-size: 21px; margin: 2em 0 0.5em; }" in STYLESHEET
    assert ".band { background: #072B33; color: #FFFFFF;" in STYLESHEET
    assert "height: 60px" in STYLESHEET
    assert "@media (min-width: 1100px)" in STYLESHEET
    assert (
        "  font-size: 15px;\n  font-variant-numeric: tabular-nums;"
        in dict(_rules(STYLESHEET))["table"]
    )
    assert (
        "td {\n"
        '  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;\n'
        "  padding: 10px;\n"
        "  border-bottom: 1px solid #E3E4E4;\n"
        "  vertical-align: top;\n"
        "}"
    ) in STYLESHEET
    assert (
        "th:first-child, td:first-child { position: sticky; left: 0; background: inherit; }\n"
        "th:first-child, td:first-child { border-right: 1px solid #E3E4E4; }"
    ) in STYLESHEET
    assert ".dek { font-size: 20px; font-weight: 400; color: #212A2A; }" in STYLESHEET
    assert "  padding-top: 12px;\n  color: #212A2A;\n}" in STYLESHEET
    assert (
        '.lim-id { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;'
        " font-size: 15px; color: #090C0C; }"
    ) in STYLESHEET


def _rules(css: str) -> list[tuple[str, str]]:
    """Each rule as (selector as written, body), with an `@media` header dropped."""
    rules = []
    for chunk in css.split("}"):
        if "{" not in chunk:
            continue
        head, body = chunk.rsplit("{", 1)
        rules.append((head.split("{")[-1].strip(), body.strip()))
    return rules


def _bold(css: str) -> set[str]:
    return {selector for selector, body in _rules(css) if "font-weight: 600" in body}


# covers: AC-46
def test_font_weight_600_only_on_headings_and_table_heads() -> None:
    assert _bold(STYLESHEET) == {"h1, h2, h3", "th"}
    bolder = STYLESHEET.replace(".toc-label { color", ".toc-label { font-weight: 600; color")
    assert bolder != STYLESHEET
    assert _bold(bolder) != {"h1, h2, h3", "th"}


# covers: AC-47
def test_mono_only_on_cells_and_the_limitation_id() -> None:
    rules = _rules(STYLESHEET)
    assert {selector for selector, body in rules if style.MONO in body} == {"td", ".lim-id"}
    assert style.FONT in dict(rules)["body"]


DENIED = re.compile(
    r":nth-(?:child|last-child|of-type)\((?!even\))|:last-child|:only-child|:has\(|\[|#"
    r"|sql|graph|q\d{2}",
    re.IGNORECASE,
)


def _picks_out_one(css: str) -> list[str]:
    return [selector for selector, _ in _rules(css) if DENIED.search(selector)]


# covers: AC-49
def test_no_selector_picks_out_one_row_or_table() -> None:
    assert _picks_out_one(STYLESHEET) == []
    one_row = STYLESHEET + "tbody tr:nth-child(3) { color: #212A2A; }\n"
    assert _picks_out_one(one_row) == ["tbody tr:nth-child(3)"]
    one_store = STYLESHEET + ".sql { color: #212A2A; }\n"
    assert _picks_out_one(one_store) == [".sql"]


# covers: AC-20
def test_no_shadow_no_web_font_no_import_and_no_other_host() -> None:
    for banned in ("box-shadow", "@font-face", "@import", "url(", "gradient", "http"):
        assert banned not in STYLESHEET
    radii = [int(r) for r in re.findall(r"border-radius:\s*(\d+)px", STYLESHEET)]
    assert all(radius <= 4 for radius in radii)
    assert "@media (prefers-color-scheme" not in STYLESHEET
    assert "transition" not in STYLESHEET and "animation" not in STYLESHEET


# covers: AC-38
def test_contrast_every_text_and_background_pair() -> None:
    assert (style.BAND_TEXT, style.BAND) in TEXT_PAIRS
    for foreground, background in TEXT_PAIRS:
        assert _contrast(foreground, background) >= 4.5, (foreground, background)
    colours = set(re.findall(r"#[0-9A-F]{6}", STYLESHEET))
    assert colours == {
        style.PAGE,
        style.TEXT,
        style.HEADING,
        style.MUTED,
        style.HAIRLINE,
        style.STRIPE,
        style.BAND,
    }
