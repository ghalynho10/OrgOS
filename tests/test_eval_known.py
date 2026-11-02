"""Unit tests for q01, the registry and the three answer modes (spec 0005, AC-25, AC-28,
AC-32 to AC-36, AC-45).

q01 runs on synthetic documents built into a snapshot. The modes run on a
temporary git repo for the run record's commit. No store.
"""

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.answers import (
    RUN_RECORD,
    Prepared,
    check_registry,
    check_sources,
    check_written,
    compute,
    first_difference,
    only,
    write_all,
)
from orgos.eval.frozen import ANSWERS
from orgos.eval.guards import Check, Hashes, Pushed
from orgos.eval.known import CORPUS_PROJECTS, REGISTRY, Registered, q01
from orgos.eval.models import Question
from orgos.eval.rules import other_person_share
from orgos.eval.snapshot import Collapsed, build_snapshot
from orgos.eval.writer import map_answer, render, step

Q01_FIELDS = (
    "fields.project.key",
    "fields.created",
    "fields.resolution.name",
    "fields.assignee.key",
)
Q01 = REGISTRY["Q01"]
ONLY_Q01 = {"Q01": Q01}
AUTHOR = {"key": "u1", "displayName": "U One"}


def doc(
    key: str,
    created: object = "2013-02-01T10:00:00.000+0000",
    histories: tuple[dict[str, Any], ...] = (),
    **fields: Any,
) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {"project": {"key": key.split("-")[0], "id": "1"}, "created": created, **fields},
        "changelog": {"histories": list(histories)},
    }


def snapshot(*docs: dict[str, Any]) -> Any:
    return build_snapshot(CORPUS_PROJECTS, "key", frozenset(), docs, set(Q01_FIELDS))


def q01_value(*docs: dict[str, Any]) -> tuple[dict[str, int], list[dict[str, list[str]]]]:
    answer = q01(snapshot(*docs), Q01)
    dumped = answer.model_dump(mode="json", by_alias=True)
    return {i["id"]: i["value"]["int"] for i in dumped["value"]}, dumped["sources"]


# q01


def test_q01_counts_unresolved_unassigned_issues_per_project_with_zeros() -> None:
    counts, sources = q01_value(
        doc("BAM-1"),  # no resolution, no assignee
        doc("BAM-2", resolution=None, assignee={"key": ""}),  # null and "" are missing
        doc("BAM-3", resolution={"name": ""}, assignee=None),
        doc("BAM-4", resolution={"name": "Fixed"}),  # resolved
        doc("CWD-1", assignee={"key": "u1"}),  # assigned
        doc("CWD-2", resolution={}),  # an empty object: its name is missing
    )
    assert counts == {f"project:{p}": 0 for p in CORPUS_PROJECTS} | {
        "project:BAM": 3,
        "project:CWD": 1,
    }
    assert sources == [{"issue": ["BAM-1", "BAM-2", "BAM-3", "CWD-2"]}]


def test_q01_reads_created_in_utc_on_a_half_open_window() -> None:
    counts, sources = q01_value(
        doc("FE-1", "2012-12-31T23:30:00.000-0500"),  # 04:30 UTC on 1 January: in
        doc("FE-2", "2012-12-31T23:59:59.999+0000"),  # before the start: out
        doc("FE-3", "2013-03-31T23:59:59.999+0000"),  # in
        doc("FE-4", "2013-04-01T00:00:00.000+0000"),  # the end bound is excluded
        doc("FE-5", "2013-04-01T01:00:00.000+0200"),  # 23:00 UTC on 31 March: in
    )
    assert counts["project:FE"] == 3
    assert sources == [{"issue": ["FE-1", "FE-3", "FE-5"]}]


def test_q01_stops_on_an_issue_without_a_created_date() -> None:
    with pytest.raises(EvalError, match=r"BAM-1: fields.created holds 0 values"):
        q01(snapshot(doc("BAM-1", None)), Q01)


def test_a_scope_outside_the_corpus_stops() -> None:
    wide = Registered(q01, ("BAM", "XYZ"), (("issue",),), Q01.window)
    with pytest.raises(EvalError, match=r"outside the corpus: \['XYZ'\]"):
        q01(snapshot(doc("BAM-1")), wide)


# The registry and the source kinds (AC-28, AC-32)


def question(question_id: str = "Q01") -> Question:
    return Question.model_validate(
        {
            "id": question_id,
            "text": "<placeholder>",
            "type": "aggregate",
            "hops": 0,
            "hop_mode": "fixed",
            "shape": "map",
            "unit": "count",
            "fields": list(Q01_FIELDS),
            "reading": "<placeholder>",
            "data": "real",
        }
    )


def test_the_registry_must_match_the_question_ids() -> None:
    check_registry([question()], ONLY_Q01)
    with pytest.raises(EvalError, match=r"^registry: no function for \['Q02'\]"):
        check_registry([question(), question("Q02")], ONLY_Q01)
    with pytest.raises(EvalError, match=r"registered but not asked \['Q01'\]"):
        check_registry([], ONLY_Q01)


def test_source_steps_must_list_the_registered_kinds() -> None:
    answer = map_answer("project", {}, (step({"issue": ["A-1"], "person": ["u1"]}),))
    with pytest.raises(EvalError, match="source steps list"):
        check_sources("Q01", answer, Q01)
    check_sources("Q01", map_answer("project", {}, ()), Q01)  # an empty answer has no steps


# The modes (AC-33, AC-34, AC-35, AC-45)


@pytest.fixture(autouse=True)
def no_hook_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Under a pre-commit hook git sets GIT_INDEX_FILE and friends for the outer repo.
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)


def prepared(*docs: dict[str, Any], questions: tuple[Question, ...] = ()) -> Prepared:
    return Prepared(
        hashes=Hashes("a" * 64, None),
        pushed=Pushed("b" * 40, ("origin/main",), None, ()),
        questions=questions or (question(),),
        snapshot=snapshot(*docs),
        uniqueness=(Check("person key to one name", [], ["u1"], False, stops=False),),
        reconciliation=(Check("person id field", "key", "key", True),),
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for args in (
        ("init", "--quiet"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "test"),
        ("commit", "--quiet", "--allow-empty", "-m", "start"),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "eval").mkdir()
    return tmp_path


def spent(history_id: str, amount: int, author: str) -> dict[str, Any]:
    return {
        "id": history_id,
        "author": {"key": author, "displayName": author},
        "created": "2013-01-01T00:00:00.000+0000",
        "items": [{"field": "timespent", "fieldtype": "jira", "from": None, "to": str(amount)}],
    }


def test_only_prints_the_answer_and_its_triggers_and_writes_nothing(root: Path) -> None:
    lines = only(prepared(doc("BAM-1")), "Q01")
    assert lines[0] == "uniqueness note: person key to one name: 1 found"
    assert json.loads(lines[1])["Q01"]["sources"] == [{"issue": ["BAM-1"]}]
    # every issue in scope has no resolution and no assignee
    assert lines[2:] == (
        "Q01 trigger unfilled_field: fields.resolution.name has no filled value "
        "in the 1 issues of its scope",
        "Q01 trigger unfilled_field: fields.assignee.key has no filled value "
        "in the 1 issues of its scope",
    )
    assert sorted(p.name for p in root.rglob("*") if ".git" not in p.parts) == ["eval"]


def test_each_collapsed_key_is_reported_in_the_printed_notes_and_the_run_record(root: Path) -> None:
    run = prepared(doc("BAM-1"))
    collapsed = Collapsed("BAM-1", "low", ("high",))
    run = replace(run, snapshot=replace(run.snapshot, collapsed=(collapsed,)))
    assert only(run, "Q01")[0] == "uniqueness note: collapsed BAM-1: kept low, dropped high"
    write_all(run, root, ONLY_Q01)
    record = json.loads((root / RUN_RECORD).read_text())
    assert record["collapsed_documents"] == {"BAM-1": {"kept": "low", "dropped": ["high"]}}


def test_only_needs_a_registered_question() -> None:
    with pytest.raises(EvalError, match="Q02 is not a question"):
        only(prepared(doc("BAM-1")), "Q02")
    with pytest.raises(EvalError, match="Q02 has no registered function"):
        only(prepared(doc("BAM-1"), questions=(question(), question("Q02"))), "Q02", ONLY_Q01)


def test_a_full_run_writes_the_answers_and_the_run_record_then_check_passes(root: Path) -> None:
    run = prepared(
        doc(
            "BAM-1", assignee={"key": "u1"}, histories=(spent("1", 60, "u1"), spent("2", 30, "u2"))
        ),
        doc("BAM-2", resolution={"name": "Fixed"}),
    )
    lines = write_all(run, root, ONLY_Q01)
    assert lines[-1] == f"wrote {ANSWERS} (1 answers, 0 triggers) and {RUN_RECORD}"
    record = json.loads((root / RUN_RECORD).read_text())
    assert record["other_person_share"] == {"num": 30, "den": 90}
    assert record["git"]["dirty"] is False
    assert record["sha256"]["amendments"] is None
    assert record["pushed_checks"]["step1_branches"] == ["origin/main"]
    assert record["uniqueness"]["person key to one name"] == {
        "expected": [],
        "found": ["u1"],
        "ok": False,
    }
    assert "password" not in json.dumps(record) and "mongodb://" not in json.dumps(record)
    assert (
        check_written(run, root, ONLY_Q01)[-1] == f"check: 1 answers match {ANSWERS} byte for byte"
    )
    before = (root / ANSWERS).read_bytes()
    record_before = (root / RUN_RECORD).read_bytes()
    (root / ANSWERS).write_bytes(before.replace(b'"int": 0', b'"int": 1', 1))
    with pytest.raises(EvalError, match=rf"^check: Q01 differs from {ANSWERS}"):
        check_written(run, root, ONLY_Q01)
    assert (root / RUN_RECORD).read_bytes() == record_before


def test_check_without_answers_stops(root: Path) -> None:
    with pytest.raises(EvalError, match=f"check: {ANSWERS} not found"):
        check_written(prepared(doc("BAM-1")), root, ONLY_Q01)


def test_first_difference_names_the_header_when_only_it_differs() -> None:
    answer = compute(prepared(doc("BAM-1")), "Q01").answer
    one, other = render({"Q01": answer}, "a" * 64, None), render({"Q01": answer}, "c" * 64, None)
    assert first_difference(one, one) is None
    assert first_difference(one, other) == "the file header or its formatting"


# The other person share (AC-36)


def test_the_other_person_share_ties_to_the_smallest_key_and_skips_bots() -> None:
    entries = snapshot(
        doc("BAM-1", histories=(spent("1", 60, "b"), spent("2", 60, "a"), spent("3", -500, "c"))),
        doc("BAM-2", histories=(spent("4", 40, "bot"), spent("5", 10, "a"))),
    ).entries
    # BAM-1: a and b tie at 60, a is top, b's 60 is other; BAM-2: bot ignored, a is top
    assert other_person_share(entries, frozenset({"bot"})) == (60, 130)
    assert other_person_share((), frozenset()) == (0, 0)
