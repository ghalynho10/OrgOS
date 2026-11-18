"""The CI guard: `check` on the committed page and report, with no `docs/` (spec 0016 AC-40).

Not marked integration, so the pytest runs of pre-commit and `ci.yml` fail when
the committed page, the report or the scan disagree.
"""

import socket
from pathlib import Path

import pytest

from orgos.site import SiteError
from orgos.site.build import check
from tests.site_pages import copy_inputs


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a network call was made")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# covers: AC-40, AC-29
def test_check_passes_on_the_committed_page_with_no_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = copy_inputs(tmp_path, docs=False)
    assert not (root / "docs").exists()
    _no_network(monkeypatch)
    lines = check(root)
    assert lines[-1] == "site check passed"
    assert "emails: 0 matches" in lines
    assert "names: 0 matches" in lines


# covers: AC-40
def test_check_fails_on_a_page_that_differs_from_a_regeneration(tmp_path: Path) -> None:
    root = copy_inputs(tmp_path, docs=False)
    page = root / "site/index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("</main>", "<p>x</p></main>"))
    with pytest.raises(SiteError, match=r"site/index\.html differs from a regeneration"):
        check(root)
