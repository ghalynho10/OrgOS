"""`orgos site build`: its inputs, its outputs and every refusal (spec 0016)."""

import copy
import json
import re
import socket
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orgos.cli import EXIT_CODES, app, run
from orgos.site import SiteError
from orgos.site.build import build, check
from tests.site_pages import conclusion_text, copy_inputs, page, report_copy

CAVEAT = (
    "For none of the three question types could the test tell whether graph or SQL did better."
    " It doesn't show they're the same either."
)


def _root(tmp_path: Path, made: Any = None, conclusion: str | None = None) -> Path:
    (tmp_path / "docs/findings").mkdir(parents=True)
    (tmp_path / "docs/findings/conclusion.md").write_text(
        conclusion_text() if conclusion is None else conclusion, encoding="utf-8"
    )
    (tmp_path / "results/findings").mkdir(parents=True)
    (tmp_path / "results/findings/report.json").write_text(
        json.dumps(made if made is not None else report_copy()), encoding="utf-8"
    )
    return tmp_path


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# covers: AC-1
def test_build_writes_only_the_three_site_files(tmp_path: Path) -> None:
    root = _root(tmp_path)
    before = _files(root)
    build(root, accept=True)
    assert _files(root) - before == {"site/index.html", "site/style.css", "site/conclusion.md"}


# covers: AC-2
def test_the_copy_equals_the_source_byte_for_byte(tmp_path: Path) -> None:
    root = _root(tmp_path)
    build(root, accept=True)
    source = (root / "docs/findings/conclusion.md").read_bytes()
    assert (root / "site/conclusion.md").read_bytes() == source


# covers: AC-6
@pytest.mark.parametrize(
    ("change", "named"),
    [
        (lambda r: r.update(partial=True), "partial"),
        (lambda r: r.update(missing=["Q01-sql-1"]), "missing"),
        (lambda r: r["plain"].update(stale=["Q01"]), "stale"),
        (lambda r: r.update(schema_version=2), "schema_version"),
    ],
    ids=["partial", "missing", "stale", "schema"],
)
def test_build_refuses_a_report_that_cannot_be_published(
    tmp_path: Path, change: Any, named: str
) -> None:
    made = report_copy()
    change(made)
    with pytest.raises(SiteError, match=named):
        build(_root(tmp_path, made), accept=True)
    assert not (tmp_path / "site").exists()


# covers: AC-7
def test_accept_is_needed_with_no_copy_and_on_a_change_and_prints_no_text(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    with pytest.raises(SiteError, match="no copy exists yet") as first:
        build(root, accept=False)
    build(root, accept=True)
    assert build(root, accept=False) == ("wrote site/index.html and site/style.css",)
    changed = conclusion_text().replace("did not detect a gap", "did not detect a gap.")
    (root / "docs/findings/conclusion.md").write_text(changed, encoding="utf-8")
    with pytest.raises(SiteError, match="differs from the copy") as second:
        build(root, accept=False)
    for refusal in (first, second):
        assert "detect" not in str(refusal.value)
        assert "Graph or SQL" not in str(refusal.value)
    assert (root / "site/conclusion.md").read_text(encoding="utf-8") == conclusion_text()
    build(root, accept=True)
    assert (root / "site/conclusion.md").read_text(encoding="utf-8") == changed


# covers: AC-29
def test_no_network_no_env_and_root_from_the_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a network call was made")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    root = _root(tmp_path / "repo")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert not (elsewhere / ".env").exists()
    result = CliRunner().invoke(app, ["site", "build", "--accept", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert (root / "site/index.html").is_file()
    assert not (elsewhere / "site").exists()


# covers: AC-31
def test_build_refuses_compared_and_differences_out_of_line(tmp_path: Path) -> None:
    made = report_copy()
    rule = next(t for t in made["tests"]["full form"] if t["name"] == "noted, full form, mixed")
    rule["differences"].append(0)
    with pytest.raises(SiteError, match="AC-31"):
        build(_root(tmp_path / "a", made), accept=True)
    made = report_copy()
    made["plain"]["noted_beside_plain"]["aggregate"][0]["compared"].reverse()
    with pytest.raises(SiteError, match="AC-31"):
        build(_root(tmp_path / "b", made), accept=True)


# covers: AC-32
@pytest.mark.parametrize("ids", [["Q03"], ["Q03", "Q16", "Q17"]])
def test_zero_answer_ids_must_be_two(tmp_path: Path, ids: list[str]) -> None:
    made = report_copy()
    made["zero_answer_questions"] = ids
    with pytest.raises(SiteError, match="AC-32"):
        build(_root(tmp_path, made), accept=True)


# covers: AC-34
def test_caveat_missing_refuses(tmp_path: Path) -> None:
    text = conclusion_text()
    assert CAVEAT in text
    with pytest.raises(SiteError, match="AC-34"):
        build(_root(tmp_path, conclusion=text.replace(CAVEAT, "")), accept=True)


# covers: AC-27
def test_site_error_has_exit_code_8(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (SiteError, 8) in EXIT_CODES
    assert [code for error, code in EXIT_CODES if error is SiteError] == [8]
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exited:
        run(["site", "build"])
    assert exited.value.code == 8


# covers: AC-8
def test_check_fails_naming_a_changed_page(tmp_path: Path) -> None:
    root = _root(tmp_path)
    build(root, accept=True)
    page = root / "site/index.html"
    page.write_text(page.read_text(encoding="utf-8").replace("<h1>", "<h1> "), encoding="utf-8")
    with pytest.raises(SiteError, match=r"site/index\.html differs from a regeneration"):
        check(root)


# covers: AC-8
def test_check_fails_naming_a_changed_stylesheet(tmp_path: Path) -> None:
    root = copy_inputs(tmp_path, docs=False)
    style = root / "site/style.css"
    style.write_text(style.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SiteError, match=r"site/style\.css differs from a regeneration") as failed:
        check(root)
    assert "site/index.html differs" not in str(failed.value)


def _second_h2_dropped(text: str) -> str:
    return text.replace("\n## 2. ", "\n", 1)


# covers: AC-2, AC-3, AC-4 (the six sections are what the tables hang from)
@pytest.mark.parametrize(
    ("change", "named"),
    [
        (lambda t: t.replace("# ", "Intro\n\n# ", 1), "does not start with a heading 1"),
        (_second_h2_dropped, "has 5 heading 2 sections, not 6"),
        (lambda t: t + "\n## 7. Extra\n\nMore.\n", "has 7 heading 2 sections, not 6"),
        (lambda t: t.replace("\n## 1. ", "\nA line.\n\n## 1. ", 1), "text between its title"),
        (lambda t: t + "\n# Another title\n", "second heading 1"),
    ],
    ids=["no_h1_first", "five_sections", "seven_sections", "text_before_section_1", "second_h1"],
)
def test_build_refuses_a_conclusion_out_of_shape_and_writes_nothing(
    tmp_path: Path, change: Any, named: str
) -> None:
    changed = change(conclusion_text())
    assert changed != conclusion_text()
    with pytest.raises(SiteError, match=re.escape(named)):
        build(_root(tmp_path, conclusion=changed), accept=True)
    assert not (tmp_path / "site").exists()


def _primary(made: Any, kind: str) -> Any:
    return next(t for t in made["tests"]["full form"] if t["name"] == f"noted, full form, {kind}")


def _drop_g2_6(made: Any) -> None:
    made["limitations"] = [line for line in made["limitations"] if line["id"] != "G2-6"]


def _drop_sql_beside(made: Any) -> None:
    beside = made["plain"]["noted_beside_plain"]["mixed"]
    made["plain"]["noted_beside_plain"]["mixed"] = [e for e in beside if e["store"] != "sql"]


def _correct_as_text(made: Any) -> None:
    agent = made["tables"]["full form"]["graph"]["aggregate"]["agent"]
    agent["correct"] = str(agent["correct"])


# covers: AC-4 (each cell from its one named field, or a named refusal)
@pytest.mark.parametrize(
    ("change", "named"),
    [
        (
            lambda r: r["tests"]["full form"].append(copy.deepcopy(_primary(r, "mixed"))),
            "two primary rule entries for mixed",
        ),
        (lambda r: _primary(r, "aggregate").update(role="secondary"), "no primary rule entry"),
        (_drop_g2_6, "no limitation G2-6 (AC-33)"),
        (_drop_sql_beside, "noted_beside_plain['mixed'] has 0 entries for sql, not 1"),
        (_correct_as_text, "is not a whole number"),
        (lambda r: r.pop("zero_answer_questions"), "no field ['zero_answer_questions']"),
    ],
    ids=["two_primary", "no_primary", "no_g2_6", "store_missing", "count_as_text", "field_gone"],
)
def test_build_refuses_a_report_field_it_cannot_read_and_writes_nothing(
    tmp_path: Path, change: Any, named: str
) -> None:
    made = report_copy()
    change(made)
    with pytest.raises(SiteError, match=re.escape(named)):
        build(_root(tmp_path, made), accept=True)
    assert not (tmp_path / "site").exists()


# covers: AC-4 (the rule table reads the noted primary entries only)
def test_the_rule_table_ignores_entries_that_are_not_noted_and_primary() -> None:
    made = report_copy()
    for entry in made["tests"]["full form"]:
        if entry["role"] != "primary":
            entry.update(n=99, m=98, graph_ahead=97, sql_ahead=96)
    plain = next(t for t in made["tests"]["full form"] if t["name"] == "plain, full form, mixed")
    plain["role"] = "primary"
    assert page(made=made) == page()


def test_build_names_a_missing_report_and_the_command_that_makes_it(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "results/findings/report.json").unlink()
    with pytest.raises(
        SiteError, match=r"results/findings/report\.json is missing; run `orgos eval report` first"
    ):
        build(root, accept=True)
    assert not (root / "site").exists()


def test_build_names_a_missing_conclusion(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "docs/findings/conclusion.md").unlink()
    with pytest.raises(SiteError, match=r"docs/findings/conclusion\.md is missing"):
        build(root, accept=True)
