"""The findings report on tmp run files: reading, pairing, tables, limitations (spec 0014).

The run files here copy the field names and types of the real noted and plain run
files (a test checks them against one real file of each, keys and types only), so a
shape mismatch shows here. No known answer, no store and no model is used; the
planted values below stand in for known answer values and must never be printed.
"""

import json
import secrets
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from orgos.cli import REPORT_FILE, app, report_lines, write_report
from orgos.eval import EvalError
from orgos.eval import report as report_module
from orgos.eval.report import (
    LIMITATIONS,
    V1,
    V2,
    VERSION_HEADER,
    VERSION_SENTENCE,
    Report,
    SeriesFiles,
    VersionFiles,
    findings,
    limitation_lines,
)
from orgos.eval.score import Entry

REPO = Path(__file__).resolve().parents[1]
LOAD = "1794178447.000001"
OTHER_LOAD = "1794178447.000002"
TYPES = {
    f"Q{n:02d}": "aggregate" if n <= 7 else "path_connection" if n <= 14 else "mixed"
    for n in range(1, 21)
}
ZERO = frozenset({"Q03", "Q16"})
RUNS = 3
# Stand ins for known answer values: they sit where a real file holds an answer.
PLANTED = f"planted-{secrets.token_hex(8)}"
PLANTED_NUMBER = 987654321
PLAN = {
    "estimate_usd": 480.0,
    "model_id": "stub:model",
    "price_table": "prices.toml@2026-01-01",
    "query_timeout_s": 30,
    "request_limit": 20,
    "runs": RUNS,
    "token_limit": 400000,
    "tool_call_limit": 15,
    "usd_per_million_input": 2.0,
    "usd_per_million_output": 10.0,
}
PLAIN_PLAN = PLAN | {
    "headline": "answer_match",
    "plain_blocks_sha256": "b" * 64,
    "series": "plain",
    "version_hash": V2,
}

Score = Callable[[str, str, int], bool]


def run_file(
    question: str,
    store: str,
    run: int,
    *,
    version: str = V2,
    plain: bool = False,
    correct: bool = False,
    answer_match: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One run file with every field a real one holds, values made up."""
    match = correct if answer_match is None else answer_match
    data: dict[str, Any] = {
        "agent_version": version,
        "answer_match": match,
        "attempt_count": 1,
        "attempts": [{"outcome": "ok", "rows": 3, "seconds": 0.25, "text": f"SELECT {PLANTED}"}],
        "compare_sha256": "c" * 64,
        "correct": correct,
        "data": "real",
        "git": {"commit": "a" * 40, "dirty": False},
        "limit": None,
        "load_id": LOAD,
        "outcome": "correct" if correct else "wrong",
        "plan": dict(PLAN),
        "problem": None,
        "query_timeout_s": 30,
        "question": question,
        "response_model": "stub",
        "retries": 0,
        "run": run,
        "run_outcome": "answered",
        "seconds": 1.5,
        "source_match": correct,
        "steps": [{"extra": 0, "missing": 0}],
        "store": store,
        "store_answer": {"full": {"value": {"int": PLANTED_NUMBER}, "label": PLANTED}},
        "time": "2026-11-14T10:00:00+00:00",
        "type": TYPES[question],
        "usage": {
            "cost_usd": 0.01,
            "input_tokens": 1000,
            "output_tokens": 100,
            "requests": 2,
            "tool_calls": 1,
        },
        "zero_answer": question in ZERO,
    }
    if plain:
        data |= {"series": "plain", "plain_blocks_sha256": "b" * 64, "plan": dict(PLAIN_PLAN)}
    return data | extra


def files(
    score: Score = lambda q, s, n: False,
    *,
    version: str = V2,
    plain: bool = False,
    questions: Mapping[str, str] = TYPES,
    runs: int = RUNS,
    **extra: Any,
) -> tuple[dict[str, Any], ...]:
    """Every planned run file of a series, `score` deciding `correct` and `answer_match`."""
    return tuple(
        run_file(q, store, n, version=version, plain=plain, correct=score(q, store, n), **extra)
        for q in questions
        for store in ("sql", "graph")
        for n in range(1, runs + 1)
    )


def version(
    name: str = V2,
    score: Score = lambda q, s, n: False,
    *,
    plain: bool = False,
    plain_score: Score | None = None,
) -> VersionFiles:
    """A complete version, with its complete plain series when asked."""
    noted = SeriesFiles(RUNS, files(score, version=name))
    if not plain:
        return VersionFiles(name, noted)
    plain_files = files(plain_score or score, version=name, plain=True)
    return VersionFiles(name, noted, SeriesFiles(RUNS, plain_files))


def gold(score: Callable[[str, str], bool] = lambda q, s: True) -> tuple[Entry, ...]:
    """One gold entry per question and store."""
    return tuple(
        Entry("gold", "gold", store, q, kind, "real", "correct" if score(q, store) else "wrong")
        for q, kind in TYPES.items()
        for store in ("sql", "graph")
    )


def make(*versions: VersionFiles, load: str = LOAD) -> Report:
    return findings(versions, TYPES, load, gold())


def text(made: Report) -> str:
    return "\n".join(made.lines)


def without(found: tuple[dict[str, Any], ...], *names: str) -> tuple[dict[str, Any], ...]:
    """The files less those named `Qnn-store-n`."""
    return tuple(f for f in found if f"{f['question']}-{f['store']}-{f['run']}" not in names)


def graph_ahead_on(*questions: str) -> Score:
    """The graph correct on every run of the questions, SQL never."""
    return lambda q, s, n: s == "graph" and q in questions


AGGREGATE = tuple(q for q, k in TYPES.items() if k == "aggregate")

# Spec 0014 AC-30, Groups 1 to 4, split at each `; ` in spec order (texts frozen by rule-frozen).
SPEC_GROUPS_1_TO_4 = (
    (
        "G1-1",
        "known answers and dbt share one reading of the Jira fields",
    ),
    (
        "G1-2",
        (
            "the gold queries were model drafted blind to the answers, Q01 as a teaching pass, "
            "with counts only feedback, and Q01's pattern shaped the rest"
        ),
    ),
    (
        "G1-3",
        "`model_int` is written in SQL, so the graph may lean relational.",
    ),
    (
        "G2-1",
        (
            "n is 20, with 6 or 7 questions per type, so a test with fewer than 6 untied "
            "questions can never read `real`, and `did not detect a gap` is not `equal`"
        ),
    ),
    (
        "G2-2",
        "the family bound of AC-4 and the chance line of AC-7",
    ),
    (
        "G2-3",
        (
            "the known answer code, the gold queries and the agent come from one model family, a "
            "shared blind spot and not a bias between stores"
        ),
    ),
    (
        "G2-4",
        "the strict score is all or nothing and not comparable to published execution accuracy",
    ),
    (
        "G2-5",
        (
            "query and run times are recorded but never compared or ranked, because both stores "
            "run untuned in Docker on one laptop on a small dataset (spec 0011 AC-24)"
        ),
    ),
    (
        "G2-6",
        (
            "every scored run got the reading note, and plain runs are a lower bound that still "
            "carried shape, unit and hop limit (spec 0013 Follow-up)."
        ),
    ),
    (
        "G3-1",
        (
            "hours count positive entries only, 70 negative entries left out (about 1.5% of "
            "counted seconds, CLOV about 12%)"
        ),
    ),
    (
        "G3-2",
        "Q03 and Q16 are zero answers, shown both ways",
    ),
    (
        "G3-3",
        "Q08 is one hop",
    ),
    (
        "G3-4",
        (
            "Q10 is scored through its sources, and was told to return entities, not pairs, in "
            "both stores"
        ),
    ),
    (
        "G3-5",
        "categories are properties, not labels (spec 0008)",
    ),
    (
        "G3-6",
        (
            "Q11 mixes people and projects in one set, and the sources of an empty, zero or false"
            " answer are `[]` (spec 0012 AC-17, AC-18)"
        ),
    ),
    (
        "G3-7",
        "the literal check misses small numbers (spec 0011 Follow-up).",
    ),
    (
        "G4-1",
        "the reading note's definitions and the list of source kinds were given",
    ),
    (
        "G4-2",
        (
            "temperature, `top_p` and `top_k` were the provider default and thinking was adaptive"
            " at effort low"
        ),
    ),
    (
        "G4-3",
        "a run's label follows spec 0011 AC-10 as amended",
    ),
    (
        "G4-4",
        "cap hits, timeouts and `limit_hit` per limit are shown beside every headline",
    ),
    (
        "G4-5",
        "the agent was given the answer shape and unit of each question",
    ),
    (
        "G4-6",
        "the 400,000 token limit was chosen so the 15 call limit binds first",
    ),
    (
        "G4-7",
        (
            "and if a real bug is found in the agent when no agent version is left (the cap is "
            "three), the runs are reported as they are and the bug is named here."
        ),
    ),
)
# Spec 0014 AC-30 Group 5: id, source and date in brackets, text.
SPEC_GROUP_5 = (
    (
        "G5-1",
        (
            'spec 0015 Consequences and Follow-up, 2026-11-13; `docs/session-notes.md`, "How '
            'others do it", 2026-11-13'
        ),
        (
            "From agent version 2 the agent was told the evidence layout of each scored question,"
            " which version 1 had to infer, so version 1 and version 2 source scores are not "
            "compared, and the move from version 1 to version 2 is reported as a format change "
            "(spec 0015 changes format only)."
        ),
    ),
    (
        "G5-2",
        "spec 0015 Consequences, 2026-11-13",
        (
            "Noted runs can get a layout send back that plain runs cannot, because plain has no "
            "layout line; send backs appear in the log lines and are not recorded in run files."
        ),
    ),
    (
        "G5-3",
        "spec 0015 Consequences and Follow-up, 2026-11-13",
        (
            "Change C4 applies to Q09 only, with identical text in both stores; version 1 and "
            "version 2 Q09 counts are shown on their own line."
        ),
    ),
    (
        "G5-4",
        "spec 0015 Consequences and Follow-up, 2026-11-13",
        (
            "The layout check is soft, so a layout miss shows as `source_match` false and never "
            "as a lost answer by the layout alone; a send back can still lead the model into a "
            "structural slip, so the `no_answer` count of each version is printed (AC-33); a send"
            " back also spends model requests, so a run can end as `limit_hit` because of the "
            "layout check, and the `limit_hit` count of each version is printed beside it."
        ),
    ),
    (
        "G5-5",
        "spec 0015 Consequences, 2026-11-13, added after its review",
        (
            'Two sentences of the shared Sources text, "Each kind may point at its own result." '
            'and "A source column holds no NULL, so query a kind separately when some rows lack '
            'it.", tell the agent how to shape its result tables, with identical text for every '
            "question and both stores; spec 0015 says they edge toward method."
        ),
    ),
    (
        "G5-6",
        (
            "spec 0013 Consequences, 2026-11-11, and its Follow-up (undated); spec 0015 "
            "`index.md` line 100, 2026-11-13"
        ),
        (
            "The shared instruction text still speaks of a reading note in plain runs, in both "
            'stores, so the plain series is called "without the reading note", never "cold".'
        ),
    ),
    (
        "G5-7",
        "`docs/session-notes.md`, Q03 zero, 2026-11-11",
        (
            "Q03 is a zero answer with empty evidence, not a real zero, so a query that finds "
            "nothing scores correct and the zero says nothing about lateness; the stage reading "
            "is in the note."
        ),
    ),
    (
        "G5-8",
        "`docs/session-notes.md`, Q13 limitation, 2026-11-12",
        (
            "Q13 is scored as which sub-tasks of one issue (shape set, frozen); the score checks "
            "the sub-task IDs and, in the sources, every positive time entry on them, and does "
            "not check the summed hours, the assignee or the status."
        ),
    ),
    (
        "G5-9",
        "`docs/session-notes.md`, Q16 zero, 2026-11-13",
        (
            "Q16 is a real zero but a weak test: part of its filter never removes anything, so a "
            "query that gets that part wrong still scores correct; the stage reading is in the "
            "note."
        ),
    ),
    (
        "G5-10",
        (
            '`docs/OrgOS-carry-forward.md`, "Findings report (feature 17), additions", file dated'
            " 2026-11-03"
        ),
        (
            "`eval/data-description.md`, already published, holds real looking names and emails "
            "taken from changelog display strings of the public CC BY 4.0 dataset, and "
            "`tests/fixtures/corpus/` possibly does too (not checked); they are not newly exposed"
            " and the file is not edited."
        ),
    ),
    (
        "G5-11",
        (
            "`docs/OrgOS-carry-forward.md`, that section and its date; this spec's Consequences, "
            "2026-11-11"
        ),
        "The accuracy numbers hold only for this dataset, one model family and one load.",
    ),
    (
        "G5-12",
        "the `time` field of the run files, the git tag `rule-frozen`, checked 2026-11-14",
        (
            "The commit tagged `rule-frozen` is dated 2026-11-11 at 23:57 UTC. The six version 1 "
            "Q01 runs (2026-11-11, 16:46 to 17:02 UTC) came before it; every other agent run came"
            " after it (version 1 from 2026-11-12, version 2 from 2026-11-13, the plain series "
            "from 2026-11-14). Replay files are not agent results."
        ),
    ),
    (
        "G5-13",
        "this amendment, 2026-11-14; spec 0015 commit `20d2486`, 2026-11-13 20:59 UTC",
        (
            "The measure of the version 1 against version 2 block (`answer_match`, Q09 apart) and"
            " the `no_answer` cells were set on 2026-11-14, after the version 1, version 2 and "
            "plain results had been seen; the measure follows spec 0015 line 165, written before "
            "the first version 2 run. The first form of AC-17 (a disagreement line) was replaced "
            "on 2026-11-14, also after the results were seen, by side by side counts, justified "
            "only by the cross check's list of decisions the disagreement rule needed."
        ),
    ),
    (
        "G5-14",
        'spec 0015 `rationale.md` "How others do it" (SWE-bench row), 2026-11-13',
        (
            "The layout line reveals the structure of the evidence grading, which is the kinds "
            "and the step count, never IDs."
        ),
    ),
    (
        "G5-15",
        'spec 0015 `rationale.md` "How others do it", last row, 2026-11-13',
        (
            "The layout line is new: no benchmark read tells the model which record kinds to cite"
            " per question."
        ),
    ),
)


# The fixture copies the real run files (shape only)


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return ["list", _shape(value[0]) if value else None]
    return type(value).__name__


@pytest.mark.parametrize("plain", [False, True])
def test_the_fixture_has_the_field_names_and_types_of_a_real_run_file(plain: bool) -> None:
    folder = REPO / "results/agent_runs" / V2 / ("plain" if plain else "")
    real = json.loads((folder / "Q04-sql-1.json").read_text(encoding="utf-8"))
    made = run_file("Q04", "sql", 1, plain=plain)
    assert sorted(made) == sorted(real)
    for field in made:
        if field in ("store_answer", "limit", "problem"):
            continue
        assert _shape(made[field]) == _shape(real[field]), field


# The pairing (AC-11 to AC-14)


def test_noted_is_judged_on_correct_and_plain_on_answer_match() -> None:
    noted = files(lambda q, s, n: False)
    noted = tuple(f | {"answer_match": f["store"] == "graph"} for f in noted)
    plain = files(graph_ahead_on(*AGGREGATE), plain=True)
    made = make(VersionFiles(V2, SeriesFiles(RUNS, noted), SeriesFiles(RUNS, plain)))
    tests = {t["name"]: t for t in made.record["tests"]["full form"]}
    assert tests["noted, full form, aggregate"]["differences"] == [0] * 7
    assert tests["plain, full form, aggregate"]["differences"] == [3] * 7


def test_a_null_or_absent_column_counts_false_and_is_counted() -> None:
    found = list(files(lambda q, s, n: s == "graph"))
    found[0] = found[0] | {"correct": None}
    found[1] = {k: v for k, v in found[1].items() if k != "correct"}
    made = make(VersionFiles(V2, SeriesFiles(RUNS, tuple(found))))
    assert made.record["null_counts"] == {"noted": 2}
    assert "  noted: 2 run files with correct null or absent, counted false" in made.lines


def test_a_missing_file_or_another_load_id_is_not_compared_with_its_reason() -> None:
    older = version(V1)
    found = without(files(), "Q02-sql-3")
    found = tuple(f | {"load_id": OTHER_LOAD} if f["question"] == "Q05" else f for f in found)
    made = make(older, VersionFiles(V2, SeriesFiles(RUNS, found)))
    assert made.record["version"] == V1
    newer = findings((VersionFiles(V2, SeriesFiles(RUNS, found)),), TYPES, LOAD, gold()).record[
        "not_compared"
    ]["noted"]
    assert newer == {"Q02": "missing files", "Q05": "other load ID"}


@pytest.mark.parametrize("field", ["type", "zero_answer"])
def test_a_missing_type_or_zero_answer_refuses_naming_the_file(field: str) -> None:
    found = list(files())
    found[4] = {k: v for k, v in found[4].items() if k != field}
    with pytest.raises(EvalError, match=rf"{V2}/Q01-graph-2\.json holds no {field}"):
        make(VersionFiles(V2, SeriesFiles(RUNS, tuple(found))))


@pytest.mark.parametrize(("field", "value"), [("type", "mixed"), ("zero_answer", True)])
def test_a_disagreeing_type_or_zero_answer_refuses_naming_the_file(field: str, value: Any) -> None:
    found = list(files())
    found[2] = found[2] | {field: value}
    with pytest.raises(EvalError, match=rf"Q01-sql-3\.json disagrees with .* on {field}"):
        make(VersionFiles(V2, SeriesFiles(RUNS, tuple(found))))


def test_the_second_form_drops_the_zero_answer_questions_with_the_same_rule() -> None:
    made = make(version(V2, graph_ahead_on(*AGGREGATE)))
    full = {t["name"]: t for t in made.record["tests"]["full form"]}
    second = {t["name"]: t for t in made.record["tests"]["second form"]}
    assert full["noted, full form, aggregate"]["n"] == 7
    assert second["noted, second form, aggregate"]["n"] == 6
    assert second["noted, second form, mixed"]["n"] == 5
    assert made.record["zero_answer_questions"] == ["Q03", "Q16"]
    assert full["noted, full form, aggregate"]["real"]
    assert second["noted, second form, aggregate"]["role"] == "secondary"


# The family (AC-4, AC-5, AC-7)


def test_the_three_primary_tests_are_the_noted_full_form_type_tests() -> None:
    made = make(version(V2, plain=True))
    roles = {
        t["name"]: t["role"]
        for form in ("full form", "second form")
        for t in made.record["tests"][form]
    }
    assert sorted(name for name, role in roles.items() if role == "primary") == [
        "noted, full form, aggregate",
        "noted, full form, mixed",
        "noted, full form, path_connection",
    ]
    assert sorted(name for name, role in roles.items() if role == "context") == [
        "noted, full form, all questions",
        "noted, second form, all questions",
        "plain, full form, all questions",
        "plain, second form, all questions",
    ]
    assert all(
        role == "secondary"
        for name, role in roles.items()
        if "all questions" not in name and not (name.startswith("noted, full form"))
    )


def test_the_chance_lines_print_per_form_and_count_every_type_test() -> None:
    lines = make(version(V2, plain=True)).lines
    assert lines.count("    chance, primary family: at most 3 x 0.05 = 0.15") == 2
    assert lines.count("    chance, all 12 type tests: at most 12 x 0.05") == 2
    no_plain = make(version(V2)).lines
    assert no_plain.count("    chance, all 6 type tests: at most 6 x 0.05") == 2


def test_a_real_primary_gap_names_the_store_and_a_graph_lead_on_six_is_secondary_real() -> None:
    made = make(version(V2, graph_ahead_on(*AGGREGATE)))
    line = next(x for x in made.lines if "primary, noted, full form, aggregate" in x)
    assert line.endswith("real, graph ahead")
    assert "p = 1/64" in line


# The versions (AC-16 to AC-19, AC-29)


def test_the_last_complete_version_wins_and_a_newer_incomplete_one_is_named() -> None:
    third = "f" * 64
    partial = VersionFiles(
        third, SeriesFiles(RUNS, files(version=third, questions={"Q01": "aggregate"}))
    )
    made = make(version(V1), version(V2), partial)
    assert made.record["version"] == V2
    assert made.record["not_used"] == {third: 6}
    assert "  version ffffffff is newer and not complete (6 run files); not used" in made.lines


def test_a_plan_made_with_only_is_not_complete() -> None:
    only = VersionFiles(V2, SeriesFiles(RUNS, files(questions={"Q04": "aggregate"})))
    made = make(version(V1), only)
    assert made.record["version"] == V1


def test_no_complete_version_uses_the_newest_partial_with_no_verdict() -> None:
    found = without(files(graph_ahead_on(*AGGREGATE)), "Q01-sql-1")
    made = make(VersionFiles(V2, SeriesFiles(RUNS, found)))
    assert made.record["partial"]
    assert made.record["missing"] == [f"results/agent_runs/{V2}/Q01-sql-1.json"]
    tests = [t for form in ("full form", "second form") for t in made.record["tests"][form]]
    assert not any(t["real"] for t in tests)
    verdicts = [x for x in made.lines if x.startswith("    primary")]
    assert verdicts and all(x.endswith("partial, no verdict") for x in verdicts)
    assert not any("real," in x for x in made.lines)
    assert made.record["not_compared"]["noted"] == {"Q01": "missing files"}


def test_a_complete_plain_series_of_a_partial_version_gets_no_verdict() -> None:
    noted = without(files(graph_ahead_on(*AGGREGATE)), "Q01-sql-1")
    plain = files(graph_ahead_on(*AGGREGATE), plain=True)
    made = make(VersionFiles(V2, SeriesFiles(RUNS, noted), SeriesFiles(RUNS, plain)))
    assert made.record["partial"]
    assert made.record["plain"]["left_out"] is None
    plain_tests = [
        t
        for form in ("full form", "second form")
        for t in made.record["tests"][form]
        if t["name"].startswith("plain")
    ]
    assert plain_tests and all(t["partial"] and not t["real"] for t in plain_tests)
    verdicts = [x for x in made.lines if x.startswith("    secondary, descriptive, plain")]
    assert verdicts and all(x.endswith("partial, no verdict") for x in verdicts)
    assert not any("real," in x for x in made.lines)


def test_no_version_at_all_refuses_and_exits_with_the_eval_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(EvalError, match="holds no version"):
        findings((), TYPES, LOAD, gold())
    monkeypatch.chdir(tmp_path)
    from orgos.cli import run

    with pytest.raises(SystemExit) as exit_info:
        run(["eval", "report"])
    assert exit_info.value.code == 4


def test_a_plain_series_of_an_older_version_is_left_out_with_the_stale_line() -> None:
    made = make(version(V1, plain=True), version(V2))
    assert made.record["plain"]["left_out"] == "other version (the plain series is under 2627493a)"
    assert any(x.startswith(f"the plain series of version {V1} is stale") for x in made.lines)
    assert made.record["plain"]["noted_beside_plain"] is None
    assert not any(x.startswith("    secondary, descriptive, plain") for x in made.lines)


def test_a_plain_series_with_missing_files_or_another_load_is_left_out() -> None:
    noted = version(V2)
    plain = SeriesFiles(RUNS, without(files(plain=True), "Q20-graph-3"))
    made = make(VersionFiles(V2, noted.noted, plain))
    assert made.record["plain"]["left_out"] == "missing files (1 planned plain run files)"
    moved = SeriesFiles(RUNS, files(plain=True, load_id=OTHER_LOAD))
    assert make(VersionFiles(V2, noted.noted, moved)).record["plain"]["left_out"] == (
        "other load ID"
    )


# The version comparison (AC-17)


def test_the_block_reads_answer_match_not_correct() -> None:
    v1_files = tuple(f | {"answer_match": True, "correct": False} for f in files(version=V1))
    made = make(VersionFiles(V1, SeriesFiles(RUNS, v1_files)), version(V2))
    cells = made.record["version_comparison"]["cells"]
    assert cells["aggregate"]["graph"]["2627493a"] == {"answer_match": 21, "total": 21}
    assert cells["aggregate"]["graph"]["cae2a674"] == {"answer_match": 0, "total": 21}
    assert "  aggregate, graph: 2627493a 21/21, cae2a674 0/21" in made.lines


def test_q09_is_out_of_its_type_total_and_on_its_own_line() -> None:
    made = make(version(V1, lambda q, s, n: q == "Q09"), version(V2))
    cells = made.record["version_comparison"]["cells"]
    assert cells["path_connection"]["sql"]["2627493a"] == {"answer_match": 0, "total": 18}
    assert made.record["version_comparison_q09"]["sql"]["2627493a"] == {
        "answer_match": 3,
        "total": 3,
    }
    assert VERSION_HEADER.endswith("(Q09 is not in this type total)")
    assert VERSION_HEADER in made.lines
    assert "  Q09, SQL: 2627493a 3/3, cae2a674 0/3" in made.lines


def test_a_question_not_compared_in_both_versions_is_named_and_left_out_of_both() -> None:
    v1 = VersionFiles(V1, SeriesFiles(RUNS, without(files(version=V1), "Q02-graph-1")))
    made = make(v1, version(V2))
    block = made.record["version_comparison"]
    assert block["not_compared"] == {"Q02": "missing files in 2627493a"}
    assert block["cells"]["aggregate"]["graph"]["cae2a674"]["total"] == 18
    assert block["partial"] == {"2627493a": True, "cae2a674": False}
    assert "  aggregate, graph: 2627493a (partial) 0/18, cae2a674 0/18" in made.lines


def test_the_sentence_prints_once_under_the_block_and_once_in_the_json() -> None:
    made = make(version(V1), version(V2))
    assert made.lines.count(VERSION_SENTENCE) == 1
    index = made.lines.index(VERSION_SENTENCE)
    assert made.lines[index - 1].startswith("  ")
    assert made.record["version_comparison_sentence"] == VERSION_SENTENCE
    assert json.dumps(made.record).count(VERSION_SENTENCE) == 1
    assert {"version_comparison", "version_comparison_q09", "version_comparison_sentence"} <= set(
        made.record
    )
    block = [x for x in made.lines[made.lines.index(VERSION_HEADER) : index]]
    assert not any("disagree" in x or "difference" in x for x in block)


def test_a_third_version_leaves_the_block_unchanged() -> None:
    third = "f" * 64
    two = make(version(V1), version(V2))
    three = make(version(V1), version(V2), version(third))
    assert three.record["version"] == third
    for key in ("version_comparison", "version_comparison_q09", "version_comparison_sentence"):
        assert three.record[key] == two.record[key]


def test_the_block_is_left_out_with_the_reason_when_a_hash_is_absent() -> None:
    made = make(version(V2))
    assert made.record["version_comparison"] == {
        "left_out": "2627493a not in results/agent_runs/versions.jsonl"
    }
    assert made.record["version_comparison_sentence"] is None
    assert "version comparison left out: 2627493a not in results/agent_runs/versions.jsonl" in (
        made.lines
    )
    assert VERSION_SENTENCE not in made.lines


# The tables (AC-20 to AC-26)


def test_the_tables_are_raw_counts_and_the_all_question_line_is_the_sum() -> None:
    found = files(graph_ahead_on("Q01", "Q09"))
    found = tuple(
        f
        | {
            "attempt_count": 3,
            "retries": 1,
            "attempts": [
                {"outcome": "store_timeout", "rows": 0, "seconds": 30.0, "text": "x"},
                {"outcome": "row_cap", "rows": 0, "seconds": 1.0, "text": "x"},
                {"outcome": "ok", "rows": 1, "seconds": 1.0, "text": "x"},
            ],
        }
        if f["question"] == "Q01" and f["store"] == "graph"
        else f
        for f in found
    )
    found = tuple(
        f | {"run_outcome": "limit_hit", "limit": "requests"}
        if (f["question"], f["store"], f["run"]) == ("Q15", "sql", 1)
        else f
        for f in found
    )
    made = make(VersionFiles(V2, SeriesFiles(RUNS, found)))
    graph = made.record["tables"]["full form"]["graph"]
    assert graph["aggregate"]["agent"]["correct"] == 3
    assert graph["aggregate"]["agent"]["total"] == 21
    assert graph["aggregate"]["agent"]["attempts"] == 3 * 3 + 6 * 3
    assert graph["aggregate"]["agent"]["runs_with_a_retry"] == 3
    assert graph["aggregate"]["agent"]["attempts_timed_out"] == 3
    assert graph["aggregate"]["agent"]["attempts_at_row_cap"] == 3
    assert graph["all questions"]["agent"]["correct"] == 6
    assert graph["all questions"]["agent"]["total"] == 60
    assert graph["all questions"]["gold"] == {"correct": 20, "total": 20, "correct_not_reviewed": 0}
    sql = made.record["tables"]["full form"]["sql"]
    assert sql["mixed"]["agent"]["limit_hit"] == {"requests": 1, "tool_calls": 0, "tokens": 0}
    assert made.record["tables"]["second form"]["graph"]["aggregate"]["agent"]["total"] == 18
    assert made.record["tables"]["second form"]["graph"]["aggregate"]["gold"]["total"] == 6
    line = next(x for x in made.lines if x.startswith("    aggregate, graph: gold 7/7"))
    assert "agent 3/21 correct, attempts 27, runs with a retry 3" in line
    assert "attempts timed out 3, at the row cap 3, limit_hit requests 0" in line


def test_pass_k_counts_questions_with_every_run_and_with_none() -> None:
    made = make(version(V2, lambda q, s, n: q == "Q01" or (q == "Q02" and n == 1), plain=True))
    cell = made.record["pass_k"]["noted"]["sql"]["aggregate"]
    assert cell == {"every_run": 1, "no_run": 5, "questions": 7}
    assert "plain" in made.record["pass_k"]


def test_the_source_diagnostic_sums_missing_and_extra_and_marks_not_recorded() -> None:
    found = tuple(
        f | {"steps": [{"missing": 2, "extra": 1}, {"missing": 0, "extra": 4}]}
        if f["question"] == "Q01"
        else f | {"steps": []}
        if f["question"] == "Q03"
        else f
        for f in files()
    )
    made = make(VersionFiles(V2, SeriesFiles(RUNS, found)))
    diagnostic = made.record["source_diagnostic"]
    assert diagnostic["questions"]["sql"]["Q01"] == {"missing": 6, "extra": 15}
    assert diagnostic["questions"]["sql"]["Q03"] is None
    assert diagnostic["types"]["sql"]["aggregate"] == {
        "counts": {"missing": 6, "extra": 15},
        "not_recorded": ["Q03"],
    }
    assert "  SQL, Q03: not recorded" in made.lines
    assert not any("ratio" in x or "%" in x for x in made.lines if "diagnostic" in x)


def test_noted_beside_plain_is_a_lower_bound_and_the_drop_is_untested() -> None:
    made = make(version(V2, lambda q, s, n: True, plain=True, plain_score=lambda q, s, n: n == 1))
    lines = text(made)
    assert "noted beside plain, answer_match; plain is a lower bound" in lines
    assert "  aggregate, graph: noted 21/21, plain 7/21 (lower bound)" in lines
    assert (
        "  drop from noted to plain, graph, lower bound, untested: noted 60/60, plain 20/60"
        " answer_match"
    ) in made.lines
    plain_tests = [t for t in made.record["tests"]["full form"] if t["name"].startswith("plain")]
    assert {t["role"] for t in plain_tests} == {"secondary", "context"}


# The no_answer cells (AC-33)


def test_the_no_answer_cells_count_runs_by_their_label() -> None:
    found = list(without(files(), "Q20-graph-3"))
    labels = {
        0: {"run_outcome": "no_answer"},
        1: {"run_outcome": "limit_hit", "limit": "tool_calls"},
        2: {"run_outcome": None},
    }
    for index, change in labels.items():
        found[index] = found[index] | change
    found[3] = {k: v for k, v in found[3].items() if k != "run_outcome"}
    made = make(VersionFiles(V2, SeriesFiles(RUNS, tuple(found))))
    sql = made.record["no_answer_cells"][V2]["sql"]
    assert sql == {
        "no_answer": 1,
        "limit_hit": 1,
        "limit_hit_by_limit": {"requests": 0, "tool_calls": 1, "tokens": 0},
        "missing": 0,
        "no_label": 1,
        "total": 60,
    }
    graph = made.record["no_answer_cells"][V2]["graph"]
    assert (graph["missing"], graph["no_label"], graph["no_answer"]) == (1, 1, 0)
    assert set(make(version(V1), version(V2)).record["no_answer_cells"]) == {V1, V2}
    assert any(x.startswith("  cae2a674, partial, graph: no_answer 0") for x in made.lines)
    assert any("descriptive, outside the rule" in x for x in made.lines)


# The limitations (AC-30, AC-34)


def test_groups_one_to_four_are_the_frozen_texts_in_spec_order() -> None:
    mine = tuple((line.id, line.text) for line in LIMITATIONS if line.group < 5)
    assert mine == SPEC_GROUPS_1_TO_4


def test_group_five_is_the_spec_ids_sources_and_texts() -> None:
    mine = tuple((line.id, line.source, line.text) for line in LIMITATIONS if line.group == 5)
    assert mine == SPEC_GROUP_5


def test_every_limitation_carries_its_group_id_and_a_source() -> None:
    for line in LIMITATIONS:
        assert line.id.startswith(f"G{line.group}-")
        assert line.source
    assert [line.id for line in LIMITATIONS] == [
        *(f"G1-{n}" for n in range(1, 4)),
        *(f"G2-{n}" for n in range(1, 7)),
        *(f"G3-{n}" for n in range(1, 8)),
        *(f"G4-{n}" for n in range(1, 8)),
        *(f"G5-{n}" for n in range(1, 16)),
    ]


def test_no_group_five_line_says_equal_no_difference_or_same() -> None:
    for line in LIMITATIONS:
        if line.group == 5:
            for word in ("equal", "no difference", " same "):
                assert word not in line.text, line.id


def test_every_report_ends_with_the_limitations_printed_and_in_the_json() -> None:
    made = make(version(V2))
    assert made.lines[-len(LIMITATIONS) :] == tuple(f"  {x}" for x in limitation_lines())
    assert made.lines[-len(LIMITATIONS) - 1] == "limitations"
    assert [x["id"] for x in made.record["limitations"]] == [x.id for x in LIMITATIONS]
    assert made.lines[-1].startswith("  G5-15 [")


# The output and the guards (AC-15, AC-27, AC-28, AC-32)


def test_no_conclusion_and_no_recommendation_is_printed() -> None:
    made = make(version(V1), version(V2, graph_ahead_on(*AGGREGATE), plain=True))
    body = "\n".join(made.lines[: -len(LIMITATIONS)]).lower()
    for phrase in ("conclusion", "recommend", "if your questions", " use the ", "you should"):
        assert phrase not in body


def test_no_planted_answer_value_appears_in_the_lines_or_the_json() -> None:
    made = make(version(V1), version(V2, graph_ahead_on(*AGGREGATE), plain=True))
    shown = text(made) + json.dumps(made.record)
    assert PLANTED not in shown
    assert str(PLANTED_NUMBER) not in shown


def test_the_json_has_a_schema_version_and_no_timestamp_and_the_same_files_give_the_same_bytes(
    tmp_path: Path,
) -> None:
    first = make(version(V1), version(V2, plain=True)).record
    second = make(version(V1), version(V2, plain=True)).record
    assert first["schema_version"] == 1
    path = write_report(tmp_path, first)
    before = path.read_bytes()
    write_report(tmp_path, second)
    assert path.read_bytes() == before
    assert path == tmp_path / REPORT_FILE
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".tmp-")]
    assert "2026-11-14T10:00:00" not in before.decode()


QUESTION = """
[[question]]
id = "{id}"
text = "How many issues?"
type = "{type}"
hops = 0
hop_mode = "fixed"
shape = "number"
unit = "count"
fields = ["key"]
reading = "Sources, step by step: (1) count the issues."
data = "real"
"""


def _shell_root(tmp_path: Path) -> Path:
    """A tmp repository holding only what the report reads, with planted answer values."""
    root = tmp_path / "repo"
    (root / "eval").mkdir(parents=True)
    (root / "eval/questions.toml").write_text(
        "".join(QUESTION.format(id=q, type=kind) for q, kind in TYPES.items()), encoding="utf-8"
    )
    answers = {
        q: {
            "shape": "number",
            "value": {"int": 0 if q in ZERO else PLANTED_NUMBER},
            "sources": [{"issue": [PLANTED]}],
        }
        for q in TYPES
    }
    (root / "eval/answers.json").write_text(
        json.dumps({"amendments_sha256": None, "answers": answers, "questions_sha256": "x"}),
        encoding="utf-8",
    )
    for store in ("sql_load", "graph_load"):
        record = {"status": "ok", "dlt_load_id": LOAD}
        (root / "results" / store).mkdir(parents=True)
        (root / "results" / store / "run_record.json").write_text(json.dumps(record))
    runs = root / "results/agent_runs"
    for name in (V1, V2):
        (runs / name).mkdir(parents=True)
        (runs / name / "PLAN.json").write_text(json.dumps(PLAN))
        for data in files(version=name):
            (runs / name / f"{data['question']}-{data['store']}-{data['run']}.json").write_text(
                json.dumps(data)
            )
    (runs / "versions.jsonl").write_text(
        "".join(json.dumps({"version_hash": name}) + "\n" for name in (V1, V2))
    )
    return root


def test_the_report_opens_only_the_listed_files_and_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _shell_root(tmp_path)
    opened: list[str] = []
    real_read_text = Path.read_text
    real_open = Path.open

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        opened.append(self.resolve().relative_to(root.resolve()).as_posix())
        return real_read_text(self, *args, **kwargs)

    def path_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self.resolve().relative_to(root.resolve()).as_posix())
        return real_open(self, *args, **kwargs)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the report opened a store or loaded settings")

    for target in (
        "orgos.sqlstore.query.reader_connection",
        "orgos.graphstore.query.reader_driver",
        "orgos.eval.gold.reader_connection",
        "orgos.eval.gold.reader_driver",
        "orgos.cli.load_settings",
    ):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "open", path_open)
    try:
        report_lines(root)
    except EvalError as error:
        pytest.fail(f"the report refused: {error}")
    allowed = (
        "eval/questions.toml",
        "eval/amendments.toml",
        "eval/answers.json",
        "results/agent_runs/",
        "results/sql_load/run_record.json",
        "results/graph_load/run_record.json",
        "results/gold/tries.jsonl",
        "gold/",
    )
    assert opened
    assert all(path.startswith(allowed) for path in opened), opened


def test_the_command_prints_no_planted_value_and_writes_only_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _shell_root(tmp_path)
    before = {p for p in root.rglob("*") if p.is_file()}
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as exit_info:
        app(args=["eval", "report"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    written = (root / REPORT_FILE).read_text(encoding="utf-8")
    for shown in (out, written):
        assert PLANTED not in shown
        assert str(PLANTED_NUMBER) not in shown
    assert {p for p in root.rglob("*") if p.is_file()} - before == {root / REPORT_FILE}
    assert out.rstrip().endswith(f"wrote {REPORT_FILE}")


def test_the_report_module_never_imports_the_agent() -> None:
    source = Path(report_module.__file__).read_text(encoding="utf-8")
    assert "orgos.agent" not in source


# Gaps closed by /test: version choice and plans (AC-16 to AC-19)


def test_a_plan_made_with_store_is_not_complete() -> None:
    sql_only = tuple(f for f in files() if f["store"] == "sql")
    made = make(version(V1), VersionFiles(V2, SeriesFiles(RUNS, sql_only)))
    assert made.record["version"] == V1
    assert made.record["not_used"] == {V2: 60}


def test_the_plain_series_is_not_counted_for_completeness() -> None:
    plain = SeriesFiles(RUNS, without(files(plain=True), "Q20-graph-3"))
    made = make(version(V1), VersionFiles(V2, version(V2).noted, plain))
    assert made.record["version"] == V2
    assert not made.record["partial"]


def test_a_version_with_no_plan_is_partial_and_names_its_plan() -> None:
    made = make(VersionFiles(V2, SeriesFiles(None, files())))
    assert made.record["partial"]
    assert made.record["missing"] == [f"results/agent_runs/{V2}/PLAN.json"]
    assert set(made.record["not_compared"]["noted"].values()) == {"no PLAN.json"}
    assert not any("real," in x for x in made.lines)


def test_an_older_version_with_no_plan_prints_no_counts() -> None:
    made = make(VersionFiles(V1, SeriesFiles(None, files(version=V1))), version(V2))
    assert made.record["older_versions"] == {V1: {"left_out": "no PLAN.json"}}
    assert "older version 2627493a: no PLAN.json, no counts" in made.lines
    assert made.record["no_answer_cells"][V1] == {"left_out": "no PLAN.json"}


def test_a_plain_series_with_no_plan_is_left_out() -> None:
    plain = SeriesFiles(None, files(plain=True))
    made = make(VersionFiles(V2, version(V2).noted, plain))
    assert made.record["plain"]["left_out"] == "no plain PLAN.json"
    assert "the plain tables are left out: no plain PLAN.json" in made.lines


# Gaps closed by /test: the version comparison (AC-17)


def test_the_block_prints_when_the_older_version_is_chosen() -> None:
    newer = VersionFiles(V2, SeriesFiles(RUNS, without(files(), "Q01-sql-1")))
    made = make(version(V1), newer)
    assert made.record["version"] == V1
    block = made.record["version_comparison"]
    assert block["partial"] == {"2627493a": False, "cae2a674": True}
    assert block["not_compared"] == {"Q01": "missing files in cae2a674"}
    assert made.lines.count(VERSION_SENTENCE) == 1


def test_q09_not_compared_in_one_version_is_named_on_its_line() -> None:
    v1 = VersionFiles(V1, SeriesFiles(RUNS, without(files(version=V1), "Q09-sql-2")))
    made = make(v1, version(V2))
    assert made.record["version_comparison_q09"] == {"not_compared": "missing files in 2627493a"}
    assert "  Q09: not compared (missing files in 2627493a)" in made.lines
    assert made.lines.count(VERSION_SENTENCE) == 1


# Gaps closed by /test: the tables (AC-11, AC-20, AC-22 to AC-24)


def _summed(cells: list[Mapping[str, Any]]) -> dict[str, Any]:
    total: dict[str, Any] = {}
    for cell in cells:
        for name, value in cell.items():
            total[name] = (
                _summed([total.get(name, {}), value])
                if isinstance(value, dict)
                else (total.get(name, 0) + value)
            )
    return total


def test_every_all_question_cell_is_the_sum_of_its_type_cells() -> None:
    found = files(lambda q, s, n: (q, n) in {("Q01", 1), ("Q09", 2), ("Q17", 3)})
    found = tuple(
        f | {"retries": 1, "attempt_count": 2, "run_outcome": "limit_hit", "limit": "tokens"}
        if f["question"] in ("Q04", "Q12")
        else f
        for f in found
    )
    made = make(VersionFiles(V2, SeriesFiles(RUNS, found)))
    for form, by_store in made.record["tables"].items():
        for store, cells in by_store.items():
            types = [cells[kind] for kind in ("aggregate", "path_connection", "mixed")]
            for part in ("agent", "gold"):
                assert cells["all questions"][part] == _summed([c[part] for c in types]), (
                    form,
                    store,
                    part,
                )


def test_plain_pass_k_reads_answer_match_not_correct() -> None:
    plain = tuple(f | {"answer_match": f["question"] == "Q01"} for f in files(plain=True))
    made = make(VersionFiles(V2, version(V2).noted, SeriesFiles(RUNS, plain)))
    assert made.record["pass_k"]["plain"]["sql"]["aggregate"]["every_run"] == 1
    assert made.record["pass_k"]["noted"]["sql"]["aggregate"]["every_run"] == 0


def test_a_question_with_one_run_holding_no_steps_is_not_recorded() -> None:
    found = tuple(
        {k: v for k, v in f.items() if k != "steps"}
        if (f["question"], f["store"], f["run"]) == ("Q01", "sql", 2)
        else f
        for f in files()
    )
    made = make(VersionFiles(V2, SeriesFiles(RUNS, found)))
    assert made.record["source_diagnostic"]["questions"]["sql"]["Q01"] is None
    assert made.record["source_diagnostic"]["questions"]["graph"]["Q01"] == {
        "missing": 0,
        "extra": 0,
    }


def test_noted_beside_plain_names_the_questions_not_compared_with_reasons() -> None:
    noted = tuple(f | {"load_id": OTHER_LOAD} if f["question"] == "Q05" else f for f in files())
    made = make(VersionFiles(V2, SeriesFiles(RUNS, noted), SeriesFiles(RUNS, files(plain=True))))
    assert made.record["plain"]["left_out"] is None
    line = next(x for x in made.lines if x.startswith("  aggregate, SQL: noted"))
    assert line.endswith("; not compared: Q05 (other load)")


@pytest.mark.xfail(
    strict=True,
    raises=KeyError,
    reason="bug found by /test: score._matched reads f['answer_match'], so an absent field"
    " crashes the noted beside plain table instead of counting false (AC-11); fix in its own"
    " session, then drop this mark",
)
def test_an_absent_answer_match_in_the_plain_series_counts_false() -> None:
    plain = list(files(graph_ahead_on(*AGGREGATE), plain=True))
    index = next(
        i
        for i, f in enumerate(plain)
        if (f["question"], f["store"], f["run"]) == ("Q01", "graph", 1)
    )
    plain[index] = {k: v for k, v in plain[index].items() if k != "answer_match"}
    made = make(VersionFiles(V2, version(V2).noted, SeriesFiles(RUNS, tuple(plain))))
    assert made.record["null_counts"]["plain"] == 1
    assert "  aggregate, graph: noted 0/21, plain 20/21 (lower bound)" in "\n".join(made.lines)


# Gaps closed by /test: the output and the command (AC-10, AC-27, AC-29, AC-32)


def test_a_failed_write_keeps_the_last_report_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    path = write_report(tmp_path, {"schema_version": 1})
    before = path.read_bytes()
    with pytest.raises(TypeError):
        write_report(tmp_path, {"schema_version": 1, "cell": object()})
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["report.json"]


def test_a_partial_report_from_the_command_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _shell_root(tmp_path)
    for name in (V1, V2):
        (root / "results/agent_runs" / name / "Q01-sql-1.json").unlink()
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as exit_info:
        app(args=["eval", "report"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"findings report, version cae2a674, partial, load ID {LOAD}"
    assert json.loads((root / REPORT_FILE).read_text(encoding="utf-8"))["partial"] is True


def test_the_command_logs_no_planted_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root = _shell_root(tmp_path)
    monkeypatch.chdir(root)
    caplog.set_level("DEBUG")
    from orgos.cli import run

    with pytest.raises(SystemExit) as exit_info:
        run(["eval", "report"])
    assert exit_info.value.code == 0
    assert PLANTED not in caplog.text
    assert str(PLANTED_NUMBER) not in caplog.text


def test_rule_in_a_folder_with_only_the_questions_opens_only_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/questions.toml").write_text(
        "".join(QUESTION.format(id=q, type=kind) for q, kind in TYPES.items()), encoding="utf-8"
    )
    opened: list[str] = []
    real_read_text = Path.read_text
    real_open = Path.open

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        opened.append(self.resolve().relative_to(tmp_path.resolve()).as_posix())
        return real_read_text(self, *args, **kwargs)

    def path_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self.resolve().relative_to(tmp_path.resolve()).as_posix())
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        app(args=["eval", "report", "--rule"])
    assert exit_info.value.code == 0
    assert set(opened) <= {"eval/questions.toml", "eval/amendments.toml"}
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()] == [
        "eval/questions.toml"
    ]
    out = capsys.readouterr().out.splitlines()
    assert "questions per type: aggregate 7, path_connection 7, mixed 6" in out
