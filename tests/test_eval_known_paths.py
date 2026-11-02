"""Unit tests for the path and connection questions q08 to q14 (spec 0005, AC-23, AC-24, AC-32,
AC-48, and your rulings of 2026-11-02).

Each function runs on synthetic documents built into a snapshot, with its
registered constants. No store.
"""

from typing import Any

import pytest

from orgos.eval import EvalError
from orgos.eval.answers import check_sources
from orgos.eval.known import CORPUS_PROJECTS, REGISTRY, q08
from orgos.eval.models import PathAnswer
from orgos.eval.rules import complete_paths, stored_links, undirected
from orgos.eval.snapshot import Snapshot, build_snapshot
from orgos.eval.writer import answer_triggers, dumped, path_answer, step

PATHS = frozenset(
    {
        "fields.resolution.name",
        "fields.assignee.key",
        "fields.reporter.key",
        "fields.creator.key",
    }
)


def doc(key: str, *histories: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {"project": {"key": key.split("-")[0], "id": "1"}, **fields},
        "changelog": {"histories": list(histories)},
    }


def link(
    link_id: str, *, out: str | None = None, into: str | None = None, label: str = "relates to"
) -> dict[str, Any]:
    stored: dict[str, Any] = {
        "id": link_id,
        "type": {"name": "T", "outward": label, "inward": f"inward of {label}"},
    }
    if out is not None:
        stored["outwardIssue"] = {"key": out}
    if into is not None:
        stored["inwardIssue"] = {"key": into}
    return stored


def inward(link_id: str, *, into: str, label: str) -> dict[str, Any]:
    stored = link(link_id, into=into, label="is superseded by")
    stored["type"]["inward"] = label
    return stored


def spent(history_id: str, amount: int, author: str | None = "u1") -> dict[str, Any]:
    history: dict[str, Any] = {
        "id": history_id,
        "created": "2020-01-01T00:00:00.000+0000",
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


def paths_of(answer: dict[str, Any]) -> list[list[str]]:
    return [[i.removeprefix("issue:") for i in item["path"]] for item in answer["value"]]


def test_the_registered_constants() -> None:
    constants = {
        qid: (REGISTRY[qid].scope, REGISTRY[qid].max_hops, REGISTRY[qid].anchor)
        for qid in ("Q08", "Q09", "Q10", "Q11", "Q12", "Q13", "Q14")
    }
    assert constants == {
        "Q08": (CORPUS_PROJECTS, 5, None),
        "Q09": (CORPUS_PROJECTS, 5, None),
        "Q10": (CORPUS_PROJECTS, None, None),
        "Q11": (CORPUS_PROJECTS, None, None),
        "Q12": (("CLOUD", "CLOV"), None, None),
        "Q13": (("CLOV",), None, "CLOV-108"),
        "Q14": (("CLOV",), None, "CLOV-538"),
    }


# The complete path rule (AC-48)


def directed(*edges: tuple[str, str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    forward: dict[str, list[str]] = {}
    backward: dict[str, list[str]] = {}
    for start, end in edges:
        forward.setdefault(start, []).append(end)
        backward.setdefault(end, []).append(start)
    return forward, backward


def test_only_chains_that_cannot_be_extended_are_returned() -> None:
    forward, backward = directed(("A", "B"), ("B", "C"))
    assert complete_paths(forward, backward, 5) == (("A", "B", "C"),)


def test_a_branch_gives_one_chain_per_branch() -> None:
    forward, backward = directed(("A", "B"), ("A", "C"), ("X", "C"))
    assert complete_paths(forward, backward, 5) == (("A", "B"), ("A", "C"), ("X", "C"))


def test_a_cycle_repeats_no_issue() -> None:
    forward, backward = directed(("A", "B"), ("B", "A"))
    assert complete_paths(forward, backward, 5) == (("A", "B"), ("B", "A"))


def test_a_chain_longer_than_the_cap_has_no_complete_section() -> None:
    edges = [(str(n), str(n + 1)) for n in range(6)]  # six hops
    forward, backward = directed(*edges)
    assert complete_paths(forward, backward, 5) == ()
    assert complete_paths(forward, backward, 6) == (tuple(str(n) for n in range(7)),)


def test_a_chain_of_exactly_the_cap_is_kept() -> None:
    forward, backward = directed(*[(str(n), str(n + 1)) for n in range(5)])
    assert [len(path) - 1 for path in complete_paths(forward, backward, 5)] == [5]


def test_on_an_undirected_graph_each_chain_comes_in_both_directions() -> None:
    graph = undirected([("A", "B"), ("B", "C")])
    assert complete_paths(graph, graph, 5) == (("A", "B", "C"), ("C", "B", "A"))


# The link labels


def test_a_link_keeps_both_labels_whichever_issue_stores_it() -> None:
    held_out = stored_links("A-1", [link("9", out="B-1", label="depends on")])[0]
    held_in = stored_links("B-1", [link("9", into="A-1", label="depends on")])[0]
    assert held_out == held_in
    assert (held_out.inward_key, held_out.outward_key) == ("A-1", "B-1")
    assert held_out.outward_label == "depends on"
    assert held_out.inward_label == "inward of depends on"


# Q08


DEPENDS = "depends on"


def test_q08_follows_outward_depends_on_among_unresolved_issues() -> None:
    answer = run(
        "Q08",
        doc("BAM-1", issuelinks=[link("1", out="BAM-2", label=DEPENDS)]),
        doc(
            "BAM-2",
            issuelinks=[
                link("1", into="BAM-1", label=DEPENDS),
                link("2", out="CWD-1", label=DEPENDS),
            ],
        ),
        doc("CWD-1", issuelinks=[link("2", into="BAM-2", label=DEPENDS)]),
        doc("FE-1", issuelinks=[link("3", out="FE-2", label="relates to")]),  # another label
        doc("FE-2", issuelinks=[link("3", into="FE-1", label="relates to")]),
        doc("FE-3", issuelinks=[link("4", out="FE-4", label=DEPENDS)]),  # a resolved end
        doc(
            "FE-4", resolution={"name": "Fixed"}, issuelinks=[link("4", into="FE-3", label=DEPENDS)]
        ),
        doc("FE-5", issuelinks=[link("5", out="OUT-1", label=DEPENDS)]),  # outside the corpus
    )
    assert paths_of(answer) == [["BAM-1", "BAM-2", "CWD-1"]]
    assert answer["value"][0]["sources"] == [
        {"issue": ["BAM-1", "BAM-2"], "issue_link": ["1"]},
        {"issue": ["BAM-2", "CWD-1"], "issue_link": ["2"]},
    ]
    assert answer["sources"] == []


def test_q08_counts_a_one_hop_chain_and_keeps_the_direction() -> None:
    answer = run(
        "Q08",
        doc("BAM-1", issuelinks=[link("1", into="BAM-2", label=DEPENDS)]),  # BAM-2 depends on BAM-1
        doc("BAM-2", issuelinks=[link("1", out="BAM-1", label=DEPENDS)]),
    )
    assert paths_of(answer) == [["BAM-2", "BAM-1"]]


def test_q08_excludes_a_chain_longer_than_five_hops_and_its_sections() -> None:
    docs = [
        doc(f"BAM-{n}", issuelinks=[link(str(n), out=f"BAM-{n + 1}", label=DEPENDS)])
        for n in range(1, 7)
    ]
    answer = run("Q08", *docs, doc("BAM-7"))
    assert paths_of(answer) == []
    built = REGISTRY["Q08"].function(snapshot(*docs, doc("BAM-7")), REGISTRY["Q08"])
    assert answer_triggers("Q08", built, None) == ("Q08 trigger empty_answer: the answer is empty",)


def test_a_path_with_the_wrong_step_count_stops() -> None:
    wrong: PathAnswer = path_answer(
        [(["issue:A-1", "issue:A-2", "issue:A-3"], [step({"issue": ["A-1"], "issue_link": ["1"]})])]
    )
    with pytest.raises(EvalError, match="has source steps"):
        check_sources("Q08", wrong, REGISTRY["Q08"])


def test_a_registered_max_hops_is_required_by_the_chain_function() -> None:
    spec = REGISTRY["Q08"]
    no_cap = type(spec)(q08, spec.scope, spec.steps)
    with pytest.raises(EvalError, match="no max_hops"):
        q08(snapshot(), no_cap)


# Q09


def test_q09_stores_each_chain_both_ways_over_different_assignees() -> None:
    answer = run(
        "Q09",
        doc("BAM-1", assignee={"key": "a"}, issuelinks=[link("1", out="BAM-2")]),
        doc(
            "BAM-2",
            assignee={"key": "b"},
            issuelinks=[link("1", into="BAM-1"), link("2", into="BAM-3", label="follows")],
        ),
        doc("BAM-3", assignee={"key": "a"}, issuelinks=[link("2", out="BAM-2", label="follows")]),
    )
    assert paths_of(answer) == [["BAM-1", "BAM-2", "BAM-3"], ["BAM-3", "BAM-2", "BAM-1"]]
    assert answer["value"][1]["sources"] == [
        {"issue": ["BAM-2", "BAM-3"], "issue_link": ["2"]},
        {"issue": ["BAM-1", "BAM-2"], "issue_link": ["1"]},
    ]


def test_q09_drops_same_assignee_unassigned_and_flagged_issues() -> None:
    answer = run(
        "Q09",
        doc("BAM-1", assignee={"key": "a"}, issuelinks=[link("1", out="BAM-2")]),
        doc("BAM-2", assignee={"key": "a"}, issuelinks=[link("1", into="BAM-1")]),  # same person
        doc("CWD-1", assignee={"key": "a"}, issuelinks=[link("2", out="CWD-2")]),
        doc("CWD-2", issuelinks=[link("2", into="CWD-1")]),  # unassigned
        doc("FE-1", assignee={"key": "a"}, issuelinks=[link("3", out="FE-2")]),
        doc("FE-2", assignee={"key": "bot"}, issuelinks=[link("3", into="FE-1")]),  # flagged
        flagged=frozenset({"bot"}),
    )
    assert paths_of(answer) == []


def test_q09_several_links_between_two_issues_stay_one_path() -> None:
    answer = run(
        "Q09",
        doc(
            "BAM-1",
            assignee={"key": "a"},
            issuelinks=[link("1", out="BAM-2"), link("2", into="BAM-2", label="follows")],
        ),
        doc(
            "BAM-2",
            assignee={"key": "b"},
            issuelinks=[link("1", into="BAM-1"), link("2", out="BAM-1", label="follows")],
        ),
    )
    assert paths_of(answer) == [["BAM-1", "BAM-2"], ["BAM-2", "BAM-1"]]
    assert answer["value"][0]["sources"] == [
        {"issue": ["BAM-1", "BAM-2"], "issue_link": ["1", "2"]},
    ]


# Q10


def test_q10_returns_source_assignees_and_lists_targets_in_the_sources() -> None:
    answer = run(
        "Q10",
        doc("BAM-1", assignee={"key": "s1"}, issuelinks=[link("1", out="BAM-2", label="blocks")]),
        doc("BAM-2", assignee={"key": "t1"}, issuelinks=[link("1", into="BAM-1", label="blocks")]),
        doc(  # the source holds inward "supersedes" with its target in inwardIssue
            "CWD-1",
            assignee={"key": "s2"},
            issuelinks=[inward("2", into="CWD-2", label="supersedes")],
        ),
        doc("CWD-2", assignee={"key": "t2"}),
        doc("FE-1", assignee={"key": "x"}, issuelinks=[link("3", out="FE-2", label="relates to")]),
        doc("FE-2", assignee={"key": "y"}),  # another label
        doc("FE-3", assignee={"key": "x"}, issuelinks=[link("4", out="FE-4", label="duplicates")]),
        doc("FE-4", assignee={"key": "x"}),  # the same person
    )
    assert answer["value"] == ["person:s1", "person:s2"]
    assert answer["sources"] == [
        {
            "issue": ["BAM-1", "BAM-2", "CWD-1", "CWD-2"],
            "issue_link": ["1", "2"],
            "person": ["s1", "s2", "t1", "t2"],
        }
    ]


def test_q10_reads_outward_links_only_from_their_source() -> None:
    answer = run(
        "Q10",
        doc("BAM-1", assignee={"key": "s"}, issuelinks=[link("1", into="BAM-2", label="blocks")]),
        doc("BAM-2", assignee={"key": "t"}),  # BAM-2 blocks BAM-1, so t is the source
    )
    assert answer["value"] == ["person:t"]


# Q11


def test_q11_connects_people_on_one_issue_through_any_role() -> None:
    answer = run(
        "Q11",
        doc("BAM-1", assignee={"key": "a"}, reporter={"key": "b"}),
        doc("CWD-1", spent("1", 60, "c"), spent("2", 30, "d"), spent("3", -9, "e")),
        doc("FE-1", assignee={"key": "z"}, reporter={"key": "z"}, creator={"key": "z"}),  # alone
        doc("CRUC-1", assignee={"key": "z"}, reporter={"key": "bot"}),  # a bot is never a person
        doc("CLOV-1", spent("4", 60, None), assignee={"key": "z"}),  # no author is no person
        flagged=frozenset({"bot"}),
    )
    assert answer["value"] == [
        "person:a",
        "person:b",
        "person:c",
        "person:d",
        "project:BAM",
        "project:CWD",
    ]
    assert answer["sources"] == [{"issue": ["BAM-1", "CWD-1"], "time_entry": ["1", "2"]}]


# Q12


def test_q12_returns_parents_with_two_qualifying_assignees_on_direct_children() -> None:
    answer = run(
        "Q12",
        doc("CLOUD-1"),
        doc("CLOUD-2", parent={"key": "CLOUD-1"}, assignee={"key": "a"}),
        doc("CLOUD-3", parent={"key": "CLOUD-1"}, assignee={"key": "b"}),
        doc("CLOUD-4", parent={"key": "CLOUD-1"}),  # no assignee: not contributing
        doc("CLOV-1"),
        doc("CLOV-2", parent={"key": "CLOV-1"}, assignee={"key": "a"}),
        doc("CLOV-3", parent={"key": "CLOV-1"}, assignee={"key": "a"}),  # one person only
        doc("CLOV-4"),
        doc("CLOV-5", parent={"key": "CLOV-4"}, assignee={"key": "a"}),
        doc("CLOV-6", parent={"key": "CLOV-4"}, assignee={"key": "bot"}),  # a bot is not a person
        doc("FE-1"),
        doc("FE-2", parent={"key": "FE-1"}, assignee={"key": "a"}),  # outside the scope
        doc("FE-3", parent={"key": "FE-1"}, assignee={"key": "b"}),
        flagged=frozenset({"bot"}),
    )
    assert answer["value"] == ["issue:CLOUD-1"]
    assert answer["sources"] == [
        {
            "issue": ["CLOUD-1", "CLOUD-2", "CLOUD-3"],
            "parent": ["CLOUD-2", "CLOUD-3"],
            "person": ["a", "b"],
        }
    ]


# Q13


def test_q13_returns_every_direct_child_with_their_counted_entries() -> None:
    answer = run(
        "Q13",
        doc("CLOV-108"),
        doc(
            "CLOV-1",
            spent("1", 60),
            spent("2", -10),
            spent("3", 5, "bot"),
            parent={"key": "CLOV-108"},
        ),
        doc("CLOV-2", parent={"key": "CLOV-108"}),  # no assignee and no time: still returned
        doc("CLOV-3", spent("4", 9), parent={"key": "CLOV-1"}),  # a grandchild
        flagged=frozenset({"bot"}),
    )
    assert answer["value"] == ["issue:CLOV-1", "issue:CLOV-2"]
    assert answer["sources"] == [
        {
            "issue": ["CLOV-1", "CLOV-108", "CLOV-2"],
            "parent": ["CLOV-1", "CLOV-2"],
            "time_entry": ["1"],
        }
    ]


def test_q13_stops_when_the_parent_or_a_child_is_outside_the_scope() -> None:
    with pytest.raises(EvalError, match="CLOV-108 is not an issue"):
        run("Q13", doc("CLOV-1"))
    with pytest.raises(EvalError, match=r"outside the registered scope: \['FE-1'\]"):
        run("Q13", doc("CLOV-108"), doc("FE-1", parent={"key": "CLOV-108"}))


def test_a_parent_with_no_children_is_an_empty_set_with_no_steps() -> None:
    answer = run("Q13", doc("CLOV-108"))
    assert (answer["value"], answer["sources"]) == ([], [])


# Q14


def test_q14_is_yes_when_every_direct_child_is_resolved() -> None:
    answer = run(
        "Q14",
        doc("CLOV-538"),  # the parent's own resolution is not read
        doc("CLOV-1", parent={"key": "CLOV-538"}, resolution={"name": "Fixed"}),
        doc("CLOV-2", parent={"key": "CLOV-538"}, resolution={"name": "Done"}),
        doc("CLOV-3", parent={"key": "CLOV-1"}),  # a grandchild, unresolved
    )
    assert answer["value"] is True
    assert answer["sources"] == [
        {"issue": ["CLOV-1", "CLOV-2", "CLOV-538"], "parent": ["CLOV-1", "CLOV-2"]}
    ]


@pytest.mark.parametrize("resolution", [None, {"name": ""}])
def test_q14_no_has_an_empty_step_list_and_fires_the_trigger(resolution: Any) -> None:
    docs = (
        doc("CLOV-538"),
        doc("CLOV-1", parent={"key": "CLOV-538"}, resolution={"name": "Fixed"}),
        doc("CLOV-2", parent={"key": "CLOV-538"}, resolution=resolution),
    )
    answer = run("Q14", *docs)
    assert (answer["value"], answer["sources"]) == (False, [])
    built = REGISTRY["Q14"].function(snapshot(*docs), REGISTRY["Q14"])
    assert answer_triggers("Q14", built, None) == ("Q14 trigger empty_answer: the answer is no",)


def test_q14_stops_without_a_direct_child() -> None:
    with pytest.raises(EvalError, match="CLOV-538 has no direct sub-task"):
        run("Q14", doc("CLOV-538"))
