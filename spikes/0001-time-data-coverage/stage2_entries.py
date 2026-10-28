"""Stage 2 (AC-6 to AC-11): time entries, bot flags, the fills rule, person id and the entry match.

Runs on every collection with a Stage 1 survivor (or on the collections named
on the command line, for the thin thread). Every rule comes from `rules.py`;
this file only reads Mongo through the reader login and writes results.
Throwaway spike code.

    uv run --env-file .env python spikes/0001-time-data-coverage/stage2_entries.py [COLLECTION ...]
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from typing import Any

import common

rules = common.load_rules()

KEEP_ITEMS = [rules.ENTRY_FIELD, rules.WORKLOG_FIELD]


def pipeline() -> list[dict[str, Any]]:
    """Each issue's time fields and only the history entries (and items) the entry rule reads."""
    histories = {"$ifNull": ["$changelog.histories", []]}
    return [
        {
            "$project": {
                "_id": 0,
                "key": 1,
                "fields.project.key": 1,
                "fields.timespent": 1,
                "fields.timeoriginalestimate": 1,
                "fields.timeestimate": 1,
                "changelog_total": "$changelog.total",
                "history_count": {"$size": histories},
                "histories": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": histories,
                                "as": "h",
                                "cond": {
                                    "$in": [
                                        rules.ENTRY_FIELD,
                                        {"$ifNull": ["$$h.items.field", []]},
                                    ]
                                },
                            }
                        },
                        "as": "h",
                        "in": {
                            "author": {
                                "key": "$$h.author.key",
                                "accountId": "$$h.author.accountId",
                                "name": "$$h.author.name",
                            },
                            "created": "$$h.created",
                            "items": {
                                "$filter": {
                                    "input": {"$ifNull": ["$$h.items", []]},
                                    "as": "i",
                                    "cond": {"$in": ["$$i.field", KEEP_ITEMS]},
                                }
                            },
                        },
                    }
                },
            }
        }
    ]


def measure_collection(name: str, totals: dict[str, int], survivors: set[str]) -> dict[str, Any]:
    """Read one collection and apply every Stage 2 rule to it."""
    t0 = time.monotonic()
    db = common.reader()
    kept: dict[str, list[Any]] = defaultdict(list)
    entries: list[Any] = []
    scanned = truncated = 0
    for doc in db[name].aggregate(pipeline(), allowDiskUse=True, batchSize=2000):
        scanned += 1
        total = doc.get("changelog_total")
        if isinstance(total, int) and total > doc["history_count"]:
            truncated += 1
        doc["changelog"] = {"histories": doc.pop("histories")}
        facts = rules.issue_facts(doc)
        entries.extend(facts.entries)
        if facts.entries or facts.timespent > 0:
            kept[facts.project].append(facts)

    out: dict[str, Any] = {"collection": name, "issues_scanned": scanned}
    out["record"] = {"issues_scanned": scanned, "changelogs_cut_short": truncated}
    if not entries:
        out["record"].update(eligible=False, reason="no time entries")
        out["record"]["seconds"] = round(time.monotonic() - t0, 1)
        return out

    # Person id, decided before bot flagging (AC-11), counted per entry.
    with_account = sum(e.author.account_id is not None for e in entries)
    field = rules.choose_person_id_field(with_account, len(entries))
    lacking = [e for e in entries if rules.person_id(e.author, field) is None]
    with_field = len(entries) - len(lacking)
    names_to_keys: dict[str, set[str]] = defaultdict(set)
    keys_to_names: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        if e.author.name is not None and e.author.key is not None:
            names_to_keys[e.author.name].add(e.author.key)
            keys_to_names[e.author.key].add(e.author.name)

    checks = rules.check_accounts(entries, field)
    flagged = rules.flagged_ids(checks)
    lifted = rules.flagged_ids(checks, lift_round_hour=True)
    bot_seconds, positive_seconds = rules.bot_time(entries, flagged, field)

    measures, people_rows, leaks = [], [], []
    base = rules.rule_set(0)
    for project in sorted(totals):
        issues = kept.get(project, [])
        m = rules.measure_project(project, totals[project], issues, flagged, field)
        parts = rules.failing_parts(m, base)
        m_lifted = rules.measure_project(project, totals[project], issues, lifted, field)
        lifted_parts = rules.failing_parts(m_lifted, base)
        project_entries = [e for i in issues for e in i.entries]
        p_bot, p_positive = rules.bot_time(project_entries, flagged, field)
        survivor = project in survivors
        if not survivor and project != rules.NONE_PROJECT and m.qualifying >= rules.N:
            leaks.append((name, project))
        measures.append(
            (
                name,
                project,
                m.stage1_total,
                "yes" if survivor else "no",
                m.qualifying,
                m.qualifying_remaining,
                f"{100 * m.qualifying / m.stage1_total:.2f}" if m.stage1_total else "",
                len(m.people),
                "fail" if parts else "pass",
                ";".join(parts),
                "fail" if lifted_parts else "pass",
                p_bot,
                p_positive,
            )
        )
        if "N" not in parts and "P" not in parts:
            people_rows.extend((name, project, person) for person in sorted(m.people))

    matched = mismatched = 0
    mismatches = []
    for project in sorted(kept):
        for issue in sorted(kept[project], key=lambda i: i.key):
            status = rules.match_status(issue, flagged, field)
            if status == "match":
                matched += 1
            elif status == "mismatch":
                mismatched += 1
                total_entries = sum(e.amount for e in issue.entries)
                mismatches.append((name, issue.key, project, total_entries, issue.timespent))

    out["flagged"] = [
        (
            name,
            c.account,
            c.entries,
            c.positive,
            c.top_amount,
            c.top_count,
            f"{c.top_count / c.positive:.4f}",
            "yes" if c.round_hour else "no",
        )
        for c in checks
        if c.flagged
    ]
    out["measures"] = measures
    out["people"] = people_rows
    out["mismatches"] = mismatches
    checked = matched + mismatched
    out["match"] = (name, matched, mismatched, f"{matched / checked:.4f}" if checked else "")
    out["leaks"] = leaks
    out["record"].update(
        eligible=True,
        person_id_field=field,
        entries=len(entries),
        entries_with_account_id=with_account,
        entries_with_person_id=with_field,
        person_id_share=round(with_field / len(entries), 4),
        person_id_share_is_finding=rules.person_share_is_finding(with_field, len(entries)),
        entries_lacking_person_id=len(lacking),
        seconds_lacking_person_id=sum(e.amount for e in lacking),
        positive_seconds_lacking_person_id=sum(e.amount for e in lacking if e.amount > 0),
        histories_with_two_timespent_items=sum(e.timespent_items >= 2 for e in entries),
        negative_entries=sum(e.amount < 0 for e in entries),
        zero_entries=sum(e.amount == 0 for e in entries),
        accounts=len(checks),
        flagged_accounts=len(flagged),
        flagged_round_hour=len(flagged - lifted),
        bot_positive_seconds=bot_seconds,
        positive_seconds=positive_seconds,
        names_with_two_keys={n: sorted(k) for n, k in sorted(names_to_keys.items()) if len(k) > 1},
        keys_with_two_names={k: sorted(n) for k, n in sorted(keys_to_names.items()) if len(n) > 1},
        stage1_survivors=sorted(survivors),
        dropped_projects_reaching_n=leaks,
        seconds=round(time.monotonic() - t0, 1),
    )
    return out


def main(requested: list[str]) -> None:
    """Run Stage 2 on the survivors' collections and write the Stage 2 results."""
    record = common.guard()
    counts = common.read_csv("project_counts.csv")
    totals: dict[str, dict[str, int]] = defaultdict(dict)
    survivors: dict[str, set[str]] = defaultdict(set)
    for row in counts:
        totals[row["collection"]][row["project"]] = int(row["total"])
        if rules.stage1_survives(
            row["project"], int(row["timespent_and_timeoriginalestimate"]), rules.N
        ):
            survivors[row["collection"]].add(row["project"])
    names = requested or sorted(c for c in totals if survivors[c])
    print(f"Stage 2 on {len(names)} collections: {', '.join(names)}", flush=True)

    started = time.monotonic()
    results = []
    for name in names:
        result = measure_collection(name, totals[name], survivors[name])
        r = result["record"]
        print(
            f"{name}: {r['issues_scanned']} issues, {r.get('entries', 0)} entries, "
            f"field {r.get('person_id_field')}, {r.get('flagged_accounts', 0)} flagged, "
            f"match {result.get('match', ('', '', '', ''))[3]} ({r['seconds']} s)",
            flush=True,
        )
        results.append(result)

    def rows(key: str) -> list[Any]:
        return [row for result in results for row in result.get(key, [])]

    common.write_csv(
        "project_measures.csv",
        (
            "collection",
            "project",
            "stage1_total",
            "stage1_survivor",
            "qualifying_timeoriginalestimate",
            "qualifying_timeestimate",
            "qualifying_percent",
            "people",
            "result",
            "failing_parts",
            "result_if_round_hour_flags_lifted",
            "bot_positive_seconds",
            "positive_seconds",
        ),
        rows("measures"),
    )
    common.write_csv(
        "flagged_accounts.csv",
        (
            "collection",
            "account",
            "entries",
            "positive_entries",
            "top_amount",
            "top_count",
            "top_share",
            "top_amount_is_round_hour",
        ),
        rows("flagged"),
    )
    common.write_csv(
        "entry_match.csv",
        ("collection", "matched", "mismatched", "rate"),
        [result["match"] for result in results if "match" in result],
    )
    common.write_csv(
        "entry_mismatches.csv",
        ("collection", "issue_key", "project", "entries_sum", "timespent"),
        rows("mismatches"),
    )
    common.write_csv("project_people.csv", ("collection", "project", "person"), rows("people"))
    leaks = rows("leaks")
    common.update_record(
        "stage2",
        {
            **record,
            "collections": names,
            "seconds": round(time.monotonic() - started, 1),
            "per_collection": {result["collection"]: result["record"] for result in results},
        },
    )
    if leaks:
        sys.exit(f"finding: projects dropped by Stage 1 reach N in Stage 2: {leaks}; stopping")


if __name__ == "__main__":
    main(sys.argv[1:])
