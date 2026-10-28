"""Choose the corpus (AC-12 to AC-15): link, rank all 16 collections, walk the ladder, pick.

Reads only the Stage 1 and Stage 2 results (no Mongo connection). Every
threshold and tie break comes from `rules.py`. At step 4 (best group under 3)
it writes the ranking so far and stops: the N = 20 amendment needs the
engineer's approval before any rerun. Throwaway spike code.

    uv run --env-file .env python spikes/0001-time-data-coverage/choose.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Any

import common
from common import COLLECTIONS

rules = common.load_rules()

RANKING_HEADER = (
    "ladder_step",
    "rank",
    "collection",
    "eligible",
    "ineligibility_reason",
    "passing_projects",
    "group_size",
    "group_qualifying",
    "group",
)


def load() -> tuple[
    dict[str, Any], dict[str, dict[str, Any]], dict[tuple[str, str], frozenset[str]]
]:
    """The Stage 2 record per collection, measures per collection, and contributor sets."""
    record = json.loads((common.RESULTS / "run_record.json").read_text())["stage2"]
    measures: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in common.read_csv("project_measures.csv"):
        measures[row["collection"]][row["project"]] = row
    people: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in common.read_csv("project_people.csv"):
        people[(row["collection"], row["project"])].add(row["person"])
    return record["per_collection"], measures, {k: frozenset(v) for k, v in people.items()}


def measure(row: dict[str, Any], people: frozenset[str] | None) -> Any:
    """Rebuild a ProjectMeasure; projects without a stored set failed N or P (Stage 2 kept none)."""
    count = int(row["people"])
    if people is None:
        people = frozenset(f"#{i}" for i in range(count))
    assert len(people) == count, row
    return rules.ProjectMeasure(
        row["project"],
        int(row["stage1_total"]),
        int(row["qualifying_timeoriginalestimate"]),
        int(row["qualifying_timeestimate"]),
        people,
    )


def score_step(step: int, per_collection, measures, people) -> tuple[list[Any], dict[str, Any]]:
    """Score every collection at one ladder step; return its ranking rows and the details."""
    rule_set = rules.rule_set(step)
    scores, details = [], {}
    for name in COLLECTIONS:
        info = per_collection.get(name)
        if info is None:
            details[name] = {"eligible": False, "reason": "no Stage 1 survivor", "passing": ()}
            scores.append(rules.CollectionScore(name, (), 0))
            continue
        if not info["eligible"]:
            details[name] = {"eligible": False, "reason": info["reason"], "passing": ()}
            scores.append(rules.CollectionScore(name, (), 0))
            continue
        passing = {}
        for project, row in measures[name].items():
            stored = people.get((name, project))
            m = measure(row, stored)
            if not rules.failing_parts(m, rule_set):
                assert stored is not None, (name, project)
                passing[project] = m
        qualifying = {p: m.qualifying for p, m in passing.items()}
        sets = {p: m.people for p, m in passing.items()}
        found = rules.groups(sets, rule_set.k)
        group = rules.largest_group(found, qualifying)
        scores.append(rules.CollectionScore(name, group, sum(qualifying[p] for p in group)))
        details[name] = {
            "eligible": True,
            "reason": "",
            "passing": tuple(sorted(passing)),
            "measures": passing,
        }
    ranked = rules.rank_collections(scores)
    rows = [
        (
            step,
            rank,
            s.collection,
            "yes" if details[s.collection]["eligible"] else "no",
            details[s.collection]["reason"],
            len(details[s.collection]["passing"]),
            s.size,
            s.qualifying,
            ";".join(s.group),
        )
        for rank, s in enumerate(ranked, start=1)
    ]
    return rows, {"ranked": ranked, "details": details, "rules": rule_set}


def corpus(step_in_force: int, scored: dict[str, Any], measures) -> dict[str, Any]:
    """Pick inside the top collection's largest group and describe the result."""
    rule_set = rules.rule_set(step_in_force)
    best = scored["ranked"][0]
    passing = scored["details"][best.collection]["measures"]
    qualifying = {p: m.qualifying for p, m in passing.items()}
    sets = {p: m.people for p, m in passing.items()}
    picked = rules.greedy_pick(best.group, qualifying, sets, rule_set.k)
    projects = []
    for project in picked:
        row = measures[best.collection][project]
        links = [
            {"project": other, "shared_people": len(sets[project] & sets[other])}
            for other in picked
            if other != project and rules.linked(sets[project], sets[other], rule_set.k)
        ]
        projects.append(
            {
                "key": project,
                "collection": best.collection,
                "stage1_total": int(row["stage1_total"]),
                "qualifying_timeoriginalestimate": int(row["qualifying_timeoriginalestimate"]),
                "qualifying_timeestimate": int(row["qualifying_timeestimate"]),
                "qualifying_percent": float(row["qualifying_percent"]),
                "people": int(row["people"]),
                "bot_positive_seconds": int(row["bot_positive_seconds"]),
                "positive_seconds": int(row["positive_seconds"]),
                "links": links,
            }
        )
    return {
        "ladder_step": step_in_force,
        "n": rule_set.n,
        "x_percent": rule_set.x_percent,
        "p": rule_set.p,
        "k": rule_set.k,
        "collection": best.collection,
        "group": list(best.group),
        "brief_5_to_10_met": rules.GROUP_TARGET_MIN <= len(picked) <= rules.GROUP_MAX,
        "projects": projects,
    }


def main() -> None:
    """Walk the ladder from step 0 and write the ranking, the corpus and the record."""
    record = common.guard()
    per_collection, measures, people = load()
    rows, steps = [], []
    outcome = None
    for step in (0, 1, 2):
        step_rows, scored = score_step(step, per_collection, measures, people)
        rows.extend(step_rows)
        best = scored["ranked"][0]
        verdict = rules.ladder_verdict(step, best.size)
        steps.append(
            {
                "step": step,
                "rules": vars(scored["rules"]),
                "best_collection": best.collection,
                "best_group_size": best.size,
                "verdict": verdict,
            }
        )
        print(f"step {step}: best {best.collection} group {best.size} -> {verdict}", flush=True)
        if verdict == "next":
            continue
        if verdict in ("accept", "accept_short"):
            outcome = corpus(step if verdict == "accept" else 3, scored, measures)
        break

    common.write_csv("collection_ranking.csv", RANKING_HEADER, rows)
    common.update_record("choose", {**record, "steps": steps})
    if outcome is None:
        sys.exit("step 4: best group under 3; stopping for the N = 20 amendment (AC-14)")
    path = common.RESULTS / "corpus.json"
    path.write_text(json.dumps(outcome, indent=2) + "\n")
    print(
        f"corpus: {outcome['collection']} step {outcome['ladder_step']}: "
        f"{[p['key'] for p in outcome['projects']]}"
    )


if __name__ == "__main__":
    main()
