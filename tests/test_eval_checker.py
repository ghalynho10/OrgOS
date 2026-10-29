"""Unit tests for the question checker on synthetic tables (spec 0005, AC-7 to AC-12, AC-42).

The tables are placeholders built in code, never real questions, and the field
paths are made up, so nothing here shapes the eval set.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from orgos.cli import run
from orgos.eval.checker import FieldLists, check, read_field_lists

LISTS = FieldLists(
    usable=frozenset({"x.alpha", "x.beta[].key", "changelog.items[field=timespent]"}),
    free_text=frozenset({"x.prose"}),
)


def question(n: int) -> dict[str, Any]:
    if n <= 7:
        kind = {"type": "aggregate", "hops": 0, "hop_mode": "fixed", "shape": "number"}
        kind["unit"] = "count"
    elif n <= 14:
        kind = {"type": "path_connection", "hops": 1, "hop_mode": "fixed", "shape": "set"}
        kind["unit"] = "none"
    else:
        kind = {"type": "mixed", "hops": 2, "hop_mode": "variable", "max_hops": 3}
        kind |= {"shape": "map", "unit": "seconds"}
    table = {
        "id": f"Q{n:02d}",
        "text": f"placeholder {n}",
        **kind,
        "fields": ["x.alpha"],
        "reading": f"placeholder reading {n}",
        "data": "real",
    }
    if n == 1:
        table["tracer"] = True
    return table


def valid() -> dict[str, Any]:
    return {"question": [question(n) for n in range(1, 21)]}


def with_change(index: int, **changes: Any) -> dict[str, Any]:
    data = valid()
    data["question"][index] |= changes
    return data


def without(index: int, key: str) -> dict[str, Any]:
    data = valid()
    del data["question"][index][key]
    return data


def test_a_valid_set_passes() -> None:
    assert check(valid(), LISTS) == ()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (without(4, "reading"), "Q05 reading: is missing"),
        (with_change(4, colour="red"), "Q05 colour: Extra inputs are not permitted"),
        (with_change(4, text=" "), "Q05 text: Value error, must not be empty"),
        (with_change(4, type="other"), "Q05 type:"),
        (with_change(4, hops="1"), "Q05 hops: Input should be a valid integer"),
        (with_change(4, hops=True), "Q05 hops: Input should be a valid integer"),
        (with_change(4, hops=3), "Q05 hops:"),
        (with_change(4, shape="table"), "Q05 shape:"),
        (with_change(4, unit="hours"), "Q05 unit:"),
        (with_change(4, data="synthetic"), "Q05 data: Input should be 'real'"),
        (with_change(4, data=True), "Q05 data:"),
        (with_change(4, tracer="no"), "Q05 tracer: Input should be a valid boolean"),
        (with_change(4, fields="x.alpha"), "Q05 fields:"),
        (with_change(4, fields=[]), "Q05 fields:"),
        (with_change(4, fields=[1]), "Q05 fields.0:"),
        (with_change(4, id="Q21"), "question 5 id: Value error, must be one of Q01 to Q20"),
        (
            with_change(4, unit="none"),
            "Q05 unit: shape number allows unit seconds or count or ratio",
        ),
        (with_change(9, unit="count"), "Q10 unit: shape set allows unit none"),
        (with_change(4, hops=1), "Q05 hops: type aggregate needs hops 0"),
        (with_change(9, hops=0), "Q10 hops: type path_connection needs hops 1 or more"),
        (with_change(4, shape="set", unit="none"), "Q05 shape: type aggregate allows shape number"),
        (with_change(9, shape="number", unit="count"), "Q10 shape: type path_connection allows"),
        (without(16, "max_hops"), "Q17 max_hops: is required when hop_mode is variable"),
        (with_change(4, max_hops=2), "Q05 max_hops: is allowed only when hop_mode is variable"),
        (with_change(16, max_hops=1), "Q17 max_hops:"),
        (with_change(16, max_hops=3.0), "Q17 max_hops: Input should be a valid integer"),
        (with_change(4, fields=["x.gamma"]), "Q05 fields: x.gamma is not in ## Usable fields"),
        (with_change(4, fields=["x.prose"]), "Q05 fields: x.prose is free text, not usable"),
    ],
)
def test_each_rule_broken_once_is_reported(data: dict[str, Any], expected: str) -> None:
    problems = check(data, LISTS)
    assert any(problem.startswith(expected) for problem in problems), problems


def test_three_broken_rules_are_all_listed_together() -> None:
    data = valid()
    data["question"][1]["hops"] = "1"
    data["question"][9]["data"] = "synthetic"
    data["question"][19]["fields"] = ["x.prose"]
    problems = check(data, LISTS)
    assert len(problems) == 3
    assert [p.split()[0] for p in problems] == ["Q02", "Q10", "Q20"]


def test_a_bad_value_is_reported_once_not_again_by_the_rules_across_keys() -> None:
    assert check(with_change(4, hops="1"), LISTS) == ("Q05 hops: Input should be a valid integer",)


def test_counts_of_8_6_6_fail() -> None:
    data = with_change(7, type="aggregate", hops=0, shape="number", unit="count")
    problems = check(data, LISTS)
    assert "questions.toml: 8 aggregate, expected 7" in problems
    assert "questions.toml: 6 path_connection, expected 7" in problems


def test_twenty_tables_with_unique_ids_are_required() -> None:
    data = valid()
    data["question"] = data["question"][:19]
    assert "questions.toml: 19 [[question]] tables, expected 20" in check(data, LISTS)
    assert "questions.toml: Q20 is missing" in check(data, LISTS)
    duplicate = with_change(19, id="Q19")
    assert "questions.toml: Q19 appears 2 times" in check(duplicate, LISTS)


def test_unknown_top_level_key_fails() -> None:
    assert "questions.toml: unknown top level key note" in check({**valid(), "note": 1}, LISTS)


def test_two_tracers_fail() -> None:
    problems = check(with_change(1, tracer=True), LISTS)
    assert "questions.toml: 2 questions have tracer = true, expected exactly 1" in problems
    assert "Q02 tracer: only Q01 may be the tracer" in problems


def test_a_tracer_on_q02_fails() -> None:
    data = with_change(1, tracer=True)
    del data["question"][0]["tracer"]
    assert "Q02 tracer: only Q01 may be the tracer" in check(data, LISTS)


def test_no_tracer_fails() -> None:
    data = without(0, "tracer")
    assert "questions.toml: 0 questions have tracer = true, expected exactly 1" in check(
        data, LISTS
    )


def test_a_path_connection_tracer_fails() -> None:
    data = valid()
    q01, q08 = data["question"][0], data["question"][7]
    for key in ("type", "hops", "shape", "unit"):
        q01[key], q08[key] = q08[key], q01[key]
    assert "Q01 tracer: the tracer must be an aggregate" in check(data, LISTS)


def test_tracer_false_elsewhere_is_allowed() -> None:
    assert check(with_change(4, tracer=False), LISTS) == ()


# AC-16: the field lists


def test_field_lists_are_read_from_their_two_sections_only() -> None:
    markdown = "\n".join(
        [
            "# Data description",
            "- `x.outside`",
            "## Usable fields",
            "- `x.alpha`",
            "- `changelog.items[field=timespent]` (always listed)",
            "## Free text, not usable",
            "- `x.prose`",
            "## Fill rates",
            "- `x.later`",
        ]
    )
    lists = read_field_lists(markdown)
    assert lists.usable == {"x.alpha", "changelog.items[field=timespent]"}
    assert lists.free_text == {"x.prose"}


# AC-42: amendments


def amendment(n: int = 5, **changes: Any) -> dict[str, Any]:
    before = question(n)
    after = copy.deepcopy(before) | {"reading": "placeholder reading, widened"}
    table = {
        "question": f"Q{n:02d}",
        "reason": "empty_answer",
        "before": before,
        "after": after,
        "code_output": "placeholder output",
        "reading_chosen": "placeholder",
        "date": "2026-11-01",
    }
    return table | changes


def test_a_valid_amendment_passes() -> None:
    assert check(valid(), LISTS, {"amendment": [amendment()]}) == ()


def test_an_amendment_changing_type_fails() -> None:
    after = question(5) | {"type": "mixed", "hops": 1}
    problems = check(valid(), LISTS, {"amendment": [amendment(after=after)]})
    assert "amendment 1 after.type: must keep the type of before" in problems


def test_an_amendment_changing_id_fails() -> None:
    after = question(5) | {"id": "Q06"}
    problems = check(valid(), LISTS, {"amendment": [amendment(after=after)]})
    assert "amendment 1 after.id: must keep the id of before" in problems


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"question": "Q21"}, "amendment 1 question:"),
        ({"reason": "changed_mind"}, "amendment 1 reason:"),
        ({"code_output": 3}, "amendment 1 code_output: Input should be a valid string"),
        ({"date": "1 Nov 2026"}, "amendment 1 date: Value error, must be an ISO date"),
        ({"before": "Q05"}, "amendment 1 before: must be the full question table"),
        ({"after": question(5) | {"fields": ["x.prose"]}}, "amendment 1 after.fields: x.prose"),
        ({"after": question(5) | {"hops": "0"}}, "amendment 1 after.hops: Input should be"),
    ],
)
def test_each_amendment_rule_broken_once_is_reported(
    changes: dict[str, Any], expected: str
) -> None:
    problems = check(valid(), LISTS, {"amendment": [amendment(**changes)]})
    assert any(problem.startswith(expected) for problem in problems), problems


def test_an_amendment_naming_an_absent_question_fails() -> None:
    data = valid()
    data["question"] = data["question"][:19]
    table = amendment(20)
    problems = check(data, LISTS, {"amendment": [table]})
    assert "amendment 1 question: Q20 is not a question in questions.toml" in problems


def test_set_rules_hold_with_amendments_applied() -> None:
    after = question(1) | {"tracer": False}
    problems = check(valid(), LISTS, {"amendment": [amendment(1, after=after)]})
    assert "with amendments applied: 0 questions have tracer = true, expected exactly 1" in problems


# The command


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)


def to_toml(data: dict[str, Any]) -> str:
    lines = []
    for table in data["question"]:
        lines.append("[[question]]")
        lines += [f"{key} = {_toml_value(value)}" for key, value in table.items()]
    return "\n".join(lines) + "\n"


def _write_eval(root: Path, data: dict[str, Any]) -> None:
    (root / "eval").mkdir()
    (root / "eval" / "questions.toml").write_text(to_toml(data))
    usable = "\n".join(f"- `{path}`" for path in sorted(LISTS.usable))
    free = "\n".join(f"- `{path}`" for path in sorted(LISTS.free_text))
    (root / "eval" / "data-description.md").write_text(
        f"## Usable fields\n{usable}\n## Free text, not usable\n{free}\n"
    )


def test_the_command_passes_a_valid_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_eval(tmp_path, valid())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        run(["eval", "check-questions"])
    assert exit_info.value.code == 0
    assert "check-questions PASS: 20 questions, 0 amendments" in capsys.readouterr().out


def test_the_command_lists_every_broken_rule_and_exits_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = valid()
    data["question"][1]["hops"] = "1"
    data["question"][9]["data"] = "synthetic"
    _write_eval(tmp_path, data)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        run(["eval", "check-questions"])
    assert exit_info.value.code == 4
    err = capsys.readouterr().err
    assert "check-questions FAIL: 2 broken rules" in err
    assert "Q02 hops:" in err and "Q10 data:" in err
    assert "Traceback" not in err


def test_the_command_rejects_malformed_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_eval(tmp_path, valid())
    (tmp_path / "eval" / "questions.toml").write_text("[[question]\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        run(["eval", "check-questions"])
    assert exit_info.value.code == 4
    assert "is not valid TOML" in capsys.readouterr().err
