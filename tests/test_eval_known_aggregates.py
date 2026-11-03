"""Unit tests for the aggregate questions q02 to q07 (spec 0005, AC-22 to AC-25, AC-28, AC-31,
AC-32, and your rulings of 2026-11-02).

Each function runs on synthetic documents built into a snapshot, with its
registered constants. No store.
"""

from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.answers import check_sources
from orgos.eval.known import CORPUS_PROJECTS, REGISTRY, Registered
from orgos.eval.models import Answer
from orgos.eval.snapshot import Snapshot, build_snapshot
from orgos.eval.writer import answer_triggers, count, dumped, number_answer

PATHS = frozenset(
    {
        "fields.resolution.name",
        "fields.resolutiondate",
        "fields.duedate",
        "fields.fixVersions[].released",
        "fields.timeoriginalestimate",
        "fields.assignee.key",
    }
)
NO_TIME = "2020-01-01T00:00:00.000+0000"


def doc(key: str, *histories: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {"project": {"key": key.split("-")[0], "id": "1"}, **fields},
        "changelog": {"histories": list(histories)},
    }


def spent(
    history_id: str, amount: int, author: str | None = "u1", created: str = NO_TIME
) -> dict[str, Any]:
    history: dict[str, Any] = {
        "id": history_id,
        "created": created,
        "items": [{"field": "timespent", "fieldtype": "jira", "from": None, "to": str(amount)}],
    }
    if author is not None:
        history["author"] = {"key": author, "displayName": author}
    return history


def snapshot(*docs: dict[str, Any], flagged: frozenset[str] = frozenset()) -> Snapshot:
    return build_snapshot(CORPUS_PROJECTS, "key", flagged, docs, PATHS)


def run(question_id: str, *docs: dict[str, Any], flagged: frozenset[str] = frozenset()) -> Any:
    spec = REGISTRY[question_id]
    answer = spec.function(snapshot(*docs, flagged=flagged), spec)
    check_sources(question_id, answer, spec)
    return dumped(answer)


def sources_of(answer: dict[str, Any]) -> dict[str, list[str]]:
    assert len(answer["sources"]) == 1
    source: dict[str, list[str]] = answer["sources"][0]
    return source


def map_of(answer: dict[str, Any]) -> dict[str, int]:
    return {item["id"]: item["value"]["int"] for item in answer["value"]}


def groups_of(answer: dict[str, Any]) -> list[tuple[int, list[str]]]:
    return [(group["value"]["int"], group["ids"]) for group in answer["value"]]


def test_the_registered_constants() -> None:
    constants = {
        qid: (spec.scope, spec.cut, spec.direction)
        for qid, spec in REGISTRY.items()
        if qid in ("Q02", "Q03", "Q04", "Q05", "Q06", "Q07")
    }
    assert constants == {
        "Q02": (("CWD",), None, None),
        "Q03": (("FE",), None, None),
        "Q04": (CORPUS_PROJECTS, None, None),
        "Q05": (CORPUS_PROJECTS, None, None),
        "Q06": (CORPUS_PROJECTS, 5, "descending"),
        "Q07": (CORPUS_PROJECTS, 10, "descending"),
    }


# Q02


def test_q02_counts_fixed_issues_with_an_unreleased_version_once() -> None:
    fixed = {"name": "Fixed"}
    answer = run(
        "Q02",
        doc("CWD-1", resolution=fixed, fixVersions=[{"released": False}]),
        doc("CWD-2", resolution=fixed, fixVersions=[{"released": True}, {"released": False}]),
        doc("CWD-3", resolution=fixed, fixVersions=[{"released": False}, {"released": False}]),
        doc("CWD-4", resolution=fixed, fixVersions=[{"released": True}]),
        doc("CWD-5", resolution=fixed, fixVersions=[]),
        doc("CWD-6", resolution={"name": "Won't Fix"}, fixVersions=[{"released": False}]),
        doc("CWD-7", fixVersions=[{"released": False}]),
        doc("FE-1", resolution=fixed, fixVersions=[{"released": False}]),
    )
    assert answer["value"] == {"int": 3}
    assert sources_of(answer) == {"issue": ["CWD-1", "CWD-2", "CWD-3"]}


def test_a_count_of_0_has_an_empty_step_list_and_no_trigger() -> None:
    spec = REGISTRY["Q02"]
    answer: Answer = spec.function(snapshot(doc("CWD-1")), spec)
    check_sources("Q02", answer, spec)
    assert dumped(answer) == {"shape": "number", "sources": [], "value": {"int": 0}}
    assert answer_triggers("Q02", answer, None) == ()


def test_a_non_zero_count_without_steps_stops() -> None:
    with pytest.raises(EvalError, match="source steps list"):
        check_sources("Q02", number_answer(count(1), ()), REGISTRY["Q02"])


# Q03


def late(
    key: str, resolved: str | None, due: object = "2012-05-01", **fields: Any
) -> dict[str, Any]:
    return doc(
        key,
        resolutiondate=resolved,
        duedate=due,
        **{"resolution": {"name": "Fixed"}, **fields},
    )


def test_q03_reads_a_date_only_due_value_as_midnight_utc_and_is_strict() -> None:
    answer = run(
        "Q03",
        late("FE-1", "2012-05-01T00:00:00.000+0000"),  # exactly at the due instant: not later
        late("FE-2", "2012-05-01T00:00:01.000+0000"),  # one second later
        late("FE-3", "2012-05-01T00:30:00.000+0100"),  # 23:30 UTC the day before: not later
        late("FE-4", "2012-06-01T00:00:00.000+0000", "2012-06-01T01:00:00.000+0200"),  # with offset
    )
    assert sources_of(answer) == {"issue": ["FE-2", "FE-4"]}


def test_q03_keeps_only_resolved_issues_in_the_half_open_window() -> None:
    answer = run(
        "Q03",
        late("FE-1", "2012-04-01T00:00:00.000+0000", "2012-01-01"),  # the start is in
        late("FE-2", "2012-07-01T00:00:00.000+0000", "2012-01-01"),  # the end is out
        late("FE-3", "2012-06-30T23:59:59.000+0000", "2012-01-01"),
        late("FE-4", "2012-05-01T00:00:00.000+0000", None),  # no due date
        late("FE-5", None, "2012-01-01"),  # no resolution date
        late("FE-6", "2012-05-01T00:00:00.000+0000", "2012-01-01", resolution={"name": ""}),
        late("CWD-1", "2012-05-01T00:00:00.000+0000", "2012-01-01"),
    )
    assert answer["value"] == {"int": 2}
    assert sources_of(answer) == {"issue": ["FE-1", "FE-3"]}


def test_q03_stops_on_a_due_value_it_cannot_read() -> None:
    for bad in ("2012-13-45", "2012-05-01T00:00:00"):
        with pytest.raises(EvalError, match="FE-1"):
            run("Q03", late("FE-1", "2012-05-01T00:00:00.000+0000", bad))


# Q04


def test_q04_is_logged_minus_estimates_per_project_and_lists_ruling_b_issues() -> None:
    answer = run(
        "Q04",
        doc("BAM-1", spent("1", 100), timeoriginalestimate=300),
        doc("BAM-2", spent("2", 50), spent("3", -40)),  # the negative entry is not logged time
        doc("BAM-3", timeoriginalestimate=0),  # an estimate of 0 counts as having one
        doc("BAM-4"),  # neither: not listed
        doc("BAM-5", spent("4", -10)),  # only a negative entry and no estimate: not listed
        doc("CWD-1", spent("5", 70), spent("6", 900, "bot")),
        doc("FE-1", spent("7", 20, None)),  # no author still counts in hours
        flagged=frozenset({"bot"}),
    )
    assert map_of(answer) == {f"project:{p}": 0 for p in CORPUS_PROJECTS} | {
        "project:BAM": -150,
        "project:CWD": 70,
        "project:FE": 20,
    }
    assert sources_of(answer) == {
        "issue": ["BAM-1", "BAM-2", "BAM-3", "CWD-1", "FE-1"],
        "time_entry": ["1", "2", "5", "7"],
    }


def test_q04_stops_on_an_estimate_that_is_not_an_integer() -> None:
    with pytest.raises(EvalError, match=r"BAM-1: fields.timeoriginalestimate"):
        run("Q04", doc("BAM-1", timeoriginalestimate=1.5))


# Q05


def test_q05_sums_per_author_on_issues_assigned_to_someone_else() -> None:
    other = {"key": "boss"}
    answer = run(
        "Q05",
        doc("BAM-1", spent("1", 100, "a"), spent("2", 10, "b"), assignee=other),
        doc("BAM-2", spent("3", 50, "a"), assignee={"key": "a"}),  # a logged on their own issue
        doc("BAM-3", spent("4", 70, "a")),  # unassigned
        doc("BAM-4", spent("5", 80, "a"), assignee={"key": "bot"}),  # flagged assignee
        doc("BAM-5", spent("6", 90, "bot"), assignee=other),  # flagged author
        doc("BAM-6", spent("7", 60, None), assignee=other),  # no author is not a person
        doc("CWD-1", spent("8", -30, "a"), spent("9", 5, "a"), assignee=other),  # positive only
        flagged=frozenset({"bot"}),
    )
    assert map_of(answer) == {"person:a": 105, "person:b": 10}
    assert sources_of(answer) == {"issue": ["BAM-1", "CWD-1"], "time_entry": ["1", "2", "9"]}


def test_q05_with_nobody_is_an_empty_map_with_no_steps() -> None:
    answer = run("Q05", doc("BAM-1", spent("1", 100, "a"), assignee={"key": "a"}))
    assert (answer["value"], answer["sources"]) == ([], [])


# Q06

IN_Q06 = "2009-11-01T00:00:00.000+0000"


def test_q06_sums_entries_in_the_window_on_currently_unresolved_issues() -> None:
    answer = run(
        "Q06",
        doc(
            "BAM-1",
            spent("1", 100, created=IN_Q06),
            spent("2", 999, created="2010-01-01T00:00:00.000+0000"),  # the end is out
            spent("3", 7, created="2009-10-01T00:00:00.000+0000"),  # the start is in
            spent("4", 8, created="2009-09-30T23:59:59.000-0100"),  # 00:59:59 UTC on 1 October: in
        ),
        doc("BAM-2", spent("5", 500, created=IN_Q06), resolution={"name": "Fixed"}),
        doc("BAM-3", spent("6", 50, created=IN_Q06), resolution={"name": ""}),
        doc("BAM-4", spent("7", -5, created=IN_Q06)),  # no positive entry
    )
    assert groups_of(answer) == [(115, ["issue:BAM-1"]), (50, ["issue:BAM-3"])]
    assert sources_of(answer) == {
        "issue": ["BAM-1", "BAM-3"],
        "time_entry": ["1", "3", "4", "6"],
    }


def test_q06_keeps_ties_at_the_fifth_and_lists_only_returned_records() -> None:
    docs = [doc(f"FE-{n}", spent(str(n), 100 - n, created=IN_Q06)) for n in range(1, 5)]
    docs += [doc(f"FE-{n}", spent(str(n), 10, created=IN_Q06)) for n in (5, 6)]
    docs += [doc("FE-7", spent("7", 1, created=IN_Q06))]
    answer = run("Q06", *docs)
    assert [ids for _, ids in groups_of(answer)][-1] == ["issue:FE-5", "issue:FE-6"]
    assert "FE-7" not in sources_of(answer)["issue"]
    assert "7" not in sources_of(answer)["time_entry"]
    spec: Registered = REGISTRY["Q06"]
    built = spec.function(snapshot(*docs), spec)
    assert answer_triggers("Q06", built, spec.cut)[0].startswith("Q06 trigger tie_at_cut")


# Q07


def test_q07_ranks_the_absolute_difference_and_lists_the_flat_people() -> None:
    answer = run(
        "Q07",
        doc("BAM-1", spent("1", 100, "a"), spent("2", 100, "b"), timeoriginalestimate=50),  # 150
        doc("BAM-2", spent("3", 10, "a"), spent("4", 5, None), timeoriginalestimate=1000),  # 985
        doc("BAM-3", spent("5", 10, "c"), timeoriginalestimate=0),  # an estimate of 0: out
        doc("BAM-4", spent("6", 10, "c")),  # no estimate: out
        doc("BAM-5", spent("7", -10, "c"), timeoriginalestimate=100),  # no positive entry: out
        doc("BAM-6", spent("8", 10, "bot"), timeoriginalestimate=100),  # only a bot: out
        flagged=frozenset({"bot"}),
    )
    assert groups_of(answer) == [(985, ["issue:BAM-2"]), (150, ["issue:BAM-1"])]
    assert sources_of(answer) == {
        "issue": ["BAM-1", "BAM-2"],
        "person": ["a", "b"],
        "time_entry": ["1", "2", "3", "4"],
    }


def test_q07_lists_people_of_returned_issues_only() -> None:
    docs = [
        doc(f"FE-{n}", spent(str(n), 1, f"p{n}"), timeoriginalestimate=1000 - n)
        for n in range(1, 12)
    ]
    answer = run("Q07", *docs)
    assert len(sources_of(answer)["issue"]) == 10
    assert "p11" not in sources_of(answer)["person"]
    assert "11" not in sources_of(answer)["time_entry"]


def test_q07_with_nothing_qualifying_is_empty_with_no_steps() -> None:
    answer = run("Q07", doc("BAM-1", timeoriginalestimate=100))
    assert (answer["value"], answer["sources"]) == ([], [])
