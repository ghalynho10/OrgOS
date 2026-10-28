"""Stage 1 (AC-5, AC-6): exact issue counts per (collection, project key), reconciled to the dump.

One aggregation per collection through the reader login. A collection whose
total differs from `EXPECTED_COUNTS` stops the run before anything is written.
Throwaway spike code.

    uv run --env-file .env python spikes/0001-time-data-coverage/stage1_counts.py [COLLECTION ...]
"""

from __future__ import annotations

import sys
import time

import common
from common import COLLECTIONS, EXPECTED_COUNTS

HEADER = (
    "collection",
    "project",
    "total",
    "timespent",
    "timespent_and_timeoriginalestimate",
    "timespent_and_timeestimate",
)


def _positive(path: str) -> dict[str, object]:
    # Numbers only, as a query `$gt 0` would (aggregation `$gt` alone sorts strings above 0).
    return {"$and": [{"$isNumber": path}, {"$gt": [path, 0]}]}


def _count_if(condition: dict[str, object]) -> dict[str, object]:
    return {"$sum": {"$cond": [condition, 1, 0]}}


def pipeline(none_project: str) -> list[dict[str, object]]:
    """Group every issue by its project key, counting the time field combinations."""
    key = "$fields.project.key"
    spent = _positive("$fields.timespent")
    return [
        {
            "$project": {
                "project": {
                    "$cond": [
                        {"$in": [{"$ifNull": [key, ""]}, [""]]},
                        none_project,
                        {"$toString": key},
                    ]
                },
                "spent": spent,
                "original": _positive("$fields.timeoriginalestimate"),
                "remaining": _positive("$fields.timeestimate"),
            }
        },
        {
            "$group": {
                "_id": "$project",
                "total": {"$sum": 1},
                "timespent": _count_if("$spent"),
                "spent_original": _count_if({"$and": ["$spent", "$original"]}),
                "spent_remaining": _count_if({"$and": ["$spent", "$remaining"]}),
            }
        },
    ]


def main(names: list[str]) -> None:
    """Count every requested collection, reconcile, then write `project_counts.csv`."""
    record = common.guard()
    rules = common.load_rules()
    db = common.reader()
    started = time.monotonic()
    rows = []
    timings = {}
    for name in names:
        t0 = time.monotonic()
        groups = list(db[name].aggregate(pipeline(rules.NONE_PROJECT), allowDiskUse=True))
        total = sum(group["total"] for group in groups)
        timings[name] = round(time.monotonic() - t0, 1)
        if total != EXPECTED_COUNTS[name]:
            sys.exit(f"{name}: issues sum to {total}, expected {EXPECTED_COUNTS[name]}; stopping")
        survivors = [
            g["_id"]
            for g in groups
            if rules.stage1_survives(g["_id"], g["spent_original"], rules.N)
        ]
        print(
            f"{name}: {total} issues, {len(groups)} project keys, {len(survivors)} survivors "
            f"({timings[name]} s)",
            flush=True,
        )
        rows.extend(
            (name, g["_id"], g["total"], g["timespent"], g["spent_original"], g["spent_remaining"])
            for g in groups
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    path = common.write_csv("project_counts.csv", HEADER, rows)
    common.update_record(
        "stage1",
        {
            **record,
            "collections": names,
            "seconds": round(time.monotonic() - started, 1),
            "seconds_per_collection": timings,
        },
    )
    print(f"wrote {path.relative_to(common.REPO)}")


if __name__ == "__main__":
    main(sys.argv[1:] or list(COLLECTIONS))
