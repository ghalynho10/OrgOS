"""Unit tests for the mixed questions q15 to q20 (spec 0005, AC-22 to AC-25, AC-32, and your
rulings of 2026-11-02).

Each function runs on synthetic documents built into a snapshot, with its
registered constants. No store.
"""

import datetime as dt
from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.answers import check_sources
from orgos.eval.known import CORPUS_PROJECTS, REGISTRY
from orgos.eval.snapshot import build_snapshot
from orgos.eval.writer import answer_triggers, dumped

PATHS = frozenset(
    {
        "fields.resolution.name",
        "fields.issuetype.name",
        "fields.priority.name",
        "fields.reporter.key",
        "fields.assignee.key",
    }
)
IN_WINDOW = "2009-08-01T00:00:00.000+0000"


def doc(key: str, *histories: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {"project": {"key": key.split("-")[0], "id": "1"}, **fields},
        "changelog": {"histories": list(histories)},
    }


def spent(
    history_id: str, amount: int, author: str | None = "u1", created: str = IN_WINDOW
) -> dict[str, Any]:
    history: dict[str, Any] = {
        "id": history_id,
        "created": created,
        "items": [{"field": "timespent", "fieldtype": "jira", "from": None, "to": str(amount)}],
    }
    if author is not None:
        history["author"] = {"key": author, "displayName": author}
    return history


def link(
    link_id: str, *, out: str | None = None, into: str | None = None, kind: str = "relates to"
) -> dict[str, Any]:
    stored: dict[str, Any] = {
        "id": link_id,
        "type": {"name": "T", "outward": kind, "inward": f"inward of {kind}"},
    }
    if out is not None:
        stored["outwardIssue"] = {"key": out}
    if into is not None:
        stored["inwardIssue"] = {"key": into}
    return stored


def blocked_by(link_id: str, into: str) -> dict[str, Any]:
    stored = link(link_id, into=into, kind="blocks")
    stored["type"]["inward"] = "is blocked by"
    return stored


def linked(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    """One document per issue named, each pair's link stored on its first issue, ids from 1."""
    stored: dict[str, list[dict[str, Any]]] = {}
    for number, (one, other) in enumerate(pairs, 1):
        stored.setdefault(one, []).append(link(str(number), out=other))
        stored.setdefault(other, [])
    return [doc(key, issuelinks=links) for key, links in stored.items()]


def run(question_id: str, *docs: dict[str, Any], flagged: frozenset[str] = frozenset()) -> Any:
    spec = REGISTRY[question_id]
    snapshot = build_snapshot(CORPUS_PROJECTS, "key", flagged, docs, PATHS)
    answer = spec.function(snapshot, spec)
    check_sources(question_id, answer, spec)
    return dumped(answer)


def values_of(answer: dict[str, Any]) -> dict[str, int]:
    return {item["id"]: item["value"]["int"] for item in answer["value"]}


def test_the_registered_constants() -> None:
    constants = {
        qid: (REGISTRY[qid].scope, REGISTRY[qid].cut, REGISTRY[qid].anchor)
        for qid in ("Q15", "Q16", "Q17", "Q18", "Q19", "Q20")
    }
    assert constants == {
        "Q15": (("CLOUD",), None, "CLOUD-643"),
        "Q16": (CORPUS_PROJECTS, None, None),
        "Q17": (CORPUS_PROJECTS, None, None),
        "Q18": (CORPUS_PROJECTS, None, None),
        "Q19": (CORPUS_PROJECTS, 5, None),
        "Q20": (CORPUS_PROJECTS, 5, None),
    }
    assert REGISTRY["Q17"].steps == (("issue", "issue_link"), ("person", "time_entry"))
    assert REGISTRY["Q20"].window == (
        dt.datetime(2009, 7, 1, tzinfo=dt.UTC),
        dt.datetime(2009, 10, 1, tzinfo=dt.UTC),
    )
    assert sorted(REGISTRY) == [f"Q{n:02d}" for n in range(1, 21)]


# Q15


def test_q15_sums_the_parent_and_its_direct_children_only() -> None:
    answer = run(
        "Q15",
        doc("CLOUD-643", spent("1", 100)),
        doc(
            "CLOUD-1",
            spent("2", 30, None),  # no author: counts in hours
            spent("3", -500),  # not positive
            spent("4", 7, "bot"),  # flagged
            parent={"key": "CLOUD-643"},
        ),
        doc("CLOUD-2", parent={"key": "CLOUD-643"}),  # contributes zero, still listed
        doc("CLOUD-3", spent("5", 1000), parent={"key": "CLOUD-1"}),  # a grandchild
        doc("CLOUD-4", spent("6", 1000)),  # no parent
        flagged=frozenset({"bot"}),
    )
    assert answer["value"] == {"int": 130}
    assert answer["sources"] == [
        {
            "issue": ["CLOUD-1", "CLOUD-2", "CLOUD-643"],
            "parent": ["CLOUD-1", "CLOUD-2"],
            "time_entry": ["1", "2"],
        }
    ]


def test_q15_has_one_step_even_with_a_total_of_0_and_empty_lists() -> None:
    answer = run("Q15", doc("CLOUD-643"))
    assert answer["value"] == {"int": 0}
    assert answer["sources"] == [{"issue": ["CLOUD-643"], "parent": [], "time_entry": []}]


def test_q15_stops_without_its_parent_or_on_a_child_outside_cloud() -> None:
    with pytest.raises(EvalError, match="CLOUD-643 is not an issue"):
        run("Q15", doc("CLOUD-1"))
    with pytest.raises(EvalError, match=r"outside the registered scope: \['BAM-1'\]"):
        run("Q15", doc("CLOUD-643"), doc("BAM-1", parent={"key": "CLOUD-643"}))


# Q16

BUG = {"issuetype": {"name": "Bug"}, "priority": {"name": "Highest"}}
FIXED = {"resolution": {"name": "Fixed"}}


def test_q16_follows_only_the_two_labels_from_the_bug_to_an_unresolved_issue() -> None:
    answer = run(
        "Q16",
        doc("BAM-1", issuelinks=[blocked_by("1", "BAM-9")], **BUG),
        doc("BAM-2", issuelinks=[link("2", out="CWD-9", kind="depends on")], **BUG),
        # two qualifying links, one count, both links and issues listed
        doc("BAM-3", issuelinks=[blocked_by("3", "FE-9"), blocked_by("4", "FE-8")], **BUG),
        doc("BAM-9"),
        doc("CWD-9", resolution={"name": ""}),  # "" is unresolved
        doc("FE-9"),
        doc("FE-8"),
        # the bug is the far end of "depends on": the direction does not qualify
        doc("BAM-4", **BUG),
        doc("CLOV-1", issuelinks=[link("5", out="BAM-4", kind="depends on")]),
        # another label
        doc("BAM-5", issuelinks=[link("6", out="CLOV-2")], **BUG),
        doc("CLOV-2"),
        # the linked issue is resolved, or outside the corpus
        doc("BAM-6", issuelinks=[blocked_by("7", "CLOV-3")], **BUG),
        doc("CLOV-3", **FIXED),
        doc("BAM-7", issuelinks=[blocked_by("8", "OUT-1")], **BUG),
        # not a bug, not Highest, or resolved
        doc(
            "CRUC-1",
            issuelinks=[blocked_by("9", "CLOV-2")],
            issuetype={"name": "Task"},
            priority={"name": "Highest"},
        ),
        doc(
            "CRUC-2",
            issuelinks=[blocked_by("10", "CLOV-2")],
            issuetype={"name": "Bug"},
            priority={"name": "High"},
        ),
        doc("CRUC-3", issuelinks=[blocked_by("11", "CLOV-2")], **BUG, **FIXED),
    )
    assert answer["value"] == {"int": 3}
    assert answer["sources"] == [
        {
            "issue": ["BAM-1", "BAM-2", "BAM-3", "BAM-9", "CWD-9", "FE-8", "FE-9"],
            "issue_link": ["1", "2", "3", "4"],
        }
    ]


def test_q16_a_link_stored_only_on_the_far_issue_counts_once_by_its_id() -> None:
    far = link("1", out="BAM-1", kind="blocks")
    far["type"]["inward"] = "is blocked by"
    answer = run(
        "Q16",
        doc("BAM-1", issuelinks=[blocked_by("1", "CWD-1")], **BUG),
        doc("CWD-1", issuelinks=[far]),
    )
    assert answer["value"] == {"int": 1}
    assert answer["sources"][0]["issue_link"] == ["1"]


def test_q16_a_count_of_0_has_the_empty_step_list() -> None:
    answer = run("Q16", doc("BAM-1", **BUG))
    assert answer["value"] == {"int": 0}
    assert answer["sources"] == []


# Q17


def test_q17_needs_the_same_person_on_both_issues_of_a_link_across_projects() -> None:
    answer = run(
        "Q17",
        doc(
            "BAM-1",
            spent("1", 10),
            spent("2", 5, "u2"),  # u2 is on only one end
            spent("3", -9),  # not positive
            issuelinks=[link("1", out="CWD-1"), link("2", out="FE-1")],
        ),
        doc("CWD-1", spent("4", 20)),
        doc("FE-1", spent("5", 40)),  # u1 again: BAM-1's entries still count once
        # the same project: not a pair
        doc("CLOV-1", spent("6", 1000), issuelinks=[link("3", out="CLOV-2")]),
        doc("CLOV-2", spent("7", 1000)),
        # not linked at all
        doc("CRUC-1", spent("8", 1000)),
    )
    assert values_of(answer) == {"person:u1": 70}
    assert answer["sources"] == [
        {"issue": ["BAM-1", "CWD-1", "FE-1"], "issue_link": ["1", "2"]},
        {"person": ["u1"], "time_entry": ["1", "4", "5"]},
    ]


def test_q17_counts_only_a_persons_own_issues_of_their_own_pairs() -> None:
    answer = run(
        "Q17",
        doc("BAM-1", spent("1", 10), spent("2", 1, "u2"), issuelinks=[link("1", out="CWD-1")]),
        doc("CWD-1", spent("3", 20), issuelinks=[link("2", out="FE-1")]),
        doc("FE-1", spent("4", 40, "u2"), spent("5", 2, "u2")),
    )
    # u2 is on BAM-1 and FE-1, which are not linked; CWD-1 and FE-1 share nobody
    assert values_of(answer) == {"person:u1": 30}
    assert answer["sources"][1] == {"person": ["u1"], "time_entry": ["1", "3"]}


def test_q17_a_bot_and_an_entry_with_no_author_are_no_person() -> None:
    answer = run(
        "Q17",
        doc(
            "BAM-1", spent("1", 10, "bot"), spent("2", 10, None), issuelinks=[link("1", out="FE-1")]
        ),
        doc("FE-1", spent("3", 20, "bot"), spent("4", 20, None)),
        flagged=frozenset({"bot"}),
    )
    assert answer["value"] == []
    assert answer["sources"] == []


# Q18


def test_q18_combines_both_roles_and_counts_each_project_once() -> None:
    answer = run(
        "Q18",
        doc("BAM-1", reporter={"key": "u1"}, assignee={"key": "u1"}),  # both roles, one project
        doc("BAM-2", reporter={"key": "u1"}),
        doc("CWD-1", assignee={"key": "u1"}, reporter={"key": "u2"}),  # u2 in one project only
        doc("FE-1", reporter={"key": "bot"}),
        doc("CLOV-1", reporter={"key": "bot"}, assignee={"key": ""}),
        flagged=frozenset({"bot"}),
    )
    assert values_of(answer) == {"person:u1": 2}
    assert answer["sources"] == [
        {
            "issue": ["BAM-1", "BAM-2", "CWD-1"],
            "person": ["u1"],
            "project": ["BAM", "CWD"],
        }
    ]


def test_q18_with_nobody_in_two_projects_has_the_empty_step_list() -> None:
    answer = run("Q18", doc("BAM-1", reporter={"key": "u1"}), doc("BAM-2"))
    assert answer["value"] == []
    assert answer["sources"] == []


# Q19


def test_q19_ranks_projects_and_lists_other_endpoints_outside_the_returned_projects() -> None:
    docs = linked(
        ("BAM-1", "CWD-1"),
        ("BAM-2", "CWD-2"),
        ("FE-1", "CLOV-1"),
        ("FE-2", "CLOV-2"),
        ("CRUC-1", "JRASERVER-1"),
        ("CRUC-2", "BAM-3"),
        ("CLOUD-1", "BAM-4"),
    )
    answer = run("Q19", *docs)
    groups = [(group["value"]["int"], group["ids"]) for group in answer["value"]]
    assert groups == [
        (4, ["project:BAM"]),
        (2, ["project:CLOV", "project:CRUC", "project:CWD", "project:FE"]),
    ]
    step = answer["sources"]
    assert len(step) == 1
    assert step[0]["project"] == ["BAM", "CLOV", "CRUC", "CWD", "FE"]
    assert step[0]["issue_link"] == ["1", "2", "3", "4", "5", "6", "7"]
    assert "JRASERVER-1" in step[0]["issue"] and "CLOUD-1" in step[0]["issue"]
    assert len(step[0]["issue"]) == 14


def test_q19_leaves_out_a_link_touching_no_returned_project_and_keeps_a_tie_at_the_fifth() -> None:
    spec = REGISTRY["Q19"]
    docs = linked(
        *((f"BAM-{n}", f"CWD-{n}") for n in range(1, 6)),  # BAM and CWD at 5
        *((f"FE-{n}", f"CLOV-{n}") for n in range(1, 5)),  # FE and CLOV at 4
        ("CRUC-1", "JRASERVER-1"),  # CRUC and JRASERVER tie at the fifth, both at 1
    )
    answer = run("Q19", *docs)
    ids = [i for group in answer["value"] for i in group["ids"]]
    assert ids[-2:] == ["project:CRUC", "project:JRASERVER"]
    snapshot = build_snapshot(CORPUS_PROJECTS, "key", frozenset(), docs, PATHS)
    triggers = answer_triggers("Q19", spec.function(snapshot, spec), spec.cut)
    assert triggers[0].startswith("Q19 trigger tie_at_cut: 2 items tie")


def test_q19_ignores_links_inside_a_project_and_a_link_to_outside_the_corpus() -> None:
    answer = run(
        "Q19",
        doc("BAM-1", issuelinks=[link("1", out="BAM-2"), link("2", out="OUT-1")]),
        doc("BAM-2"),
    )
    assert answer["value"] == []
    assert answer["sources"] == []


def test_q19_a_link_stored_on_both_issues_counts_each_issue_once() -> None:
    answer = run(
        "Q19",
        doc("BAM-1", issuelinks=[link("1", out="CWD-1"), link("2", out="FE-1")]),
        doc("CWD-1", issuelinks=[link("1", into="BAM-1")]),
        doc("FE-1"),
    )
    groups = [(group["value"]["int"], group["ids"]) for group in answer["value"]]
    assert groups == [(1, ["project:BAM", "project:CWD", "project:FE"])]
    assert answer["sources"][0]["issue_link"] == ["1", "2"]


# Q20


def test_q20_reads_the_window_in_utc_for_eligibility_and_the_total() -> None:
    answer = run(
        "Q20",
        doc(
            "BAM-1",
            spent("1", 10),
            spent("2", 2, created="2009-06-30T23:59:59.999+0000"),  # before the start
            spent("3", 4, created="2009-10-01T00:00:00.000+0000"),  # the end bound
            spent("4", 8, created="2009-10-01T01:00:00.000+0200"),  # 23:00 UTC: in
            spent("5", -100),
        ),
        doc("CWD-1", spent("6", 20)),
        # only in one project within the window; the other project's entry is outside it
        doc("FE-1", spent("7", 1000, "u2")),
        doc("CLOV-1", spent("8", 1000, "u2", created="2010-01-01T00:00:00.000+0000")),
    )
    groups = [(group["value"]["int"], group["ids"]) for group in answer["value"]]
    assert groups == [(38, ["person:u1"])]
    assert answer["sources"] == [
        {
            "issue": ["BAM-1", "CWD-1"],
            "person": ["u1"],
            "project": ["BAM", "CWD"],
            "time_entry": ["1", "4", "6"],
        }
    ]


def test_q20_lists_returned_people_only_and_keeps_a_tie_at_the_fifth() -> None:
    docs = [doc(f"BAM-{n}", spent(f"a{n}", 100 - n, f"u{n}")) for n in range(1, 8)]
    docs += [doc(f"CWD-{n}", spent(f"b{n}", 100 - n, f"u{n}")) for n in range(1, 5)]
    # u5 and u6 tie at the fifth; u7 is below them
    docs += [
        doc("CWD-5", spent("b5", 50, "u5")),
        doc("CWD-6", spent("b6", 51, "u6")),
        doc("CWD-7", spent("b7", 1, "u7")),
    ]
    answer = run("Q20", *docs)
    assert answer["value"][-1]["ids"] == ["person:u5", "person:u6"]
    assert answer["value"][-1]["value"] == {"int": 145}
    listed = answer["sources"][0]
    assert listed["person"] == ["u1", "u2", "u3", "u4", "u5", "u6"]
    assert "a7" not in listed["time_entry"] and "b7" not in listed["time_entry"]
    assert "BAM-7" not in listed["issue"]


def test_q20_an_entry_with_no_author_or_by_a_bot_is_no_person() -> None:
    answer = run(
        "Q20",
        doc("BAM-1", spent("1", 10, None), spent("2", 10, "bot")),
        doc("CWD-1", spent("3", 10, None), spent("4", 10, "bot")),
        flagged=frozenset({"bot"}),
    )
    assert answer["value"] == []
    assert answer["sources"] == []
