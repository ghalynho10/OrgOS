"""Every number on the page has a source: the report, the conclusion or a constant (AC-9)."""

from orgos.site.build import sources
from orgos.site.numbers import NUMBER, unsourced, visible_text
from tests.site_pages import conclusion_text, page, report


def _sources() -> tuple[str, ...]:
    return sources(conclusion_text(), report())


def test_every_number_token_on_the_page_has_a_source() -> None:
    assert unsourced(page(), _sources()) == ()


def test_a_number_from_nowhere_fails() -> None:
    scratch = page().replace("</main>", "<p>about 37 runs</p></main>")
    assert "37" not in {t for s in _sources() for t in NUMBER.findall(s)}
    assert unsourced(scratch, _sources()) == ("37",)


def test_attributes_script_and_style_are_not_read() -> None:
    scratch = (
        '<div tabindex="0"><meta content="initial-scale=1">'
        "<script>var x = 9182;</script><style>p { width: 7373px }</style>ok</div>"
    )
    assert visible_text(scratch) == ("ok",)
    assert unsourced(scratch, ()) == ()


def test_the_expression_follows_its_own_splitting() -> None:
    assert NUMBER.findall("Q04 G2-6 spec 0014 \N{MINUS SIGN}3 +2 7/8 4.0 18/21") == [
        "6",
        "0014",
        "3",
        "2",
        "7/8",
        "4.0",
        "18/21",
    ]
