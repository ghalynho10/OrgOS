"""Stage 1 of spike 0001 on a real Mongo: the counts and the `(none)` row reconcile (AC-5).

The spike is not a package, so its folder is put on the path and the
script's aggregation is run against a few synthetic issues.
"""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pymongo import MongoClient
from testcontainers.community.mongodb import MongoDbContainer

SPIKE = Path(__file__).resolve().parents[2] / "spikes" / "0001-time-data-coverage"
sys.path.insert(0, str(SPIKE))

import stage1_counts  # noqa: E402

pytestmark = pytest.mark.integration


def issue(project: Any, spent: Any = None, original: Any = None, remaining: Any = None) -> Any:
    fields: dict[str, Any] = {
        "timespent": spent,
        "timeoriginalestimate": original,
        "timeestimate": remaining,
    }
    if project is not ...:
        fields["project"] = {"key": project} if project is not None else None
    return {"fields": fields}


@pytest.fixture
def collection(mongo: MongoDbContainer) -> Iterator[Any]:
    with MongoClient(mongo.get_connection_url()) as client:
        coll = client["spike_stage1"]["Synthetic"]
        coll.drop()
        yield coll
        coll.drop()


def test_counts_and_the_none_row_reconcile(collection: Any) -> None:
    collection.insert_many(
        [
            issue("A", spent=60, original=60, remaining=60),
            issue("A", spent=60, original=0, remaining=60),
            issue("A", spent=60.5, original=1),
            issue("A", spent="60", original=60),  # a string is not a number above 0
            issue("A"),
            issue("B", spent=0, original=60),
            issue(None, spent=60, original=60),
            issue(..., spent=60),
            issue("", spent=60, remaining=60),
        ]
    )
    groups = list(collection.aggregate(stage1_counts.pipeline("(none)")))
    got = {
        g["_id"]: (g["total"], g["timespent"], g["spent_original"], g["spent_remaining"])
        for g in groups
    }
    assert got == {
        "A": (5, 3, 2, 2),
        "B": (1, 0, 0, 0),
        "(none)": (3, 3, 1, 1),
    }
    assert sum(total for total, *_ in got.values()) == collection.count_documents({})
