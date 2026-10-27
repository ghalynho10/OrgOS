"""The PASS rule and its report, plus the edge of `orgos mongo verify`.

PASS means the live collection names (ignoring `system.`) are exactly the
expected names and every live count equals its expected count. Nothing else
counts. The restore compares with `EXPECTED_COUNTS`; standalone verify
compares with the completion marker.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from orgos.config import Settings
from orgos.mongostore import MongoStoreError, mongo_errors
from orgos.mongostore.connect import root_client, secrets
from orgos.mongostore.dump import DATABASE
from orgos.mongostore.marker import live_counts, read_marker

Status = Literal["ok", "DIFF", "MISSING", "EXTRA"]


@dataclass(frozen=True)
class Row:
    """One collection's expected and live count; `None` where it is absent."""

    name: str
    expected: int | None
    live: int | None

    @property
    def status(self) -> Status:
        if self.expected is None:
            return "EXTRA"
        if self.live is None:
            return "MISSING"
        return "ok" if self.live == self.expected else "DIFF"


@dataclass(frozen=True)
class Comparison:
    """Every expected and live collection, sorted by name with EXTRA rows last."""

    rows: tuple[Row, ...]

    @property
    def passed(self) -> bool:
        return all(row.status == "ok" for row in self.rows)

    @property
    def differences(self) -> tuple[Row, ...]:
        return tuple(row for row in self.rows if row.status != "ok")

    @property
    def expected_total(self) -> int:
        return sum(row.expected or 0 for row in self.rows)

    @property
    def live_total(self) -> int:
        return sum(row.live or 0 for row in self.rows)


def compare(expected: Mapping[str, int], live: Mapping[str, int]) -> Comparison:
    """Compare live counts with expected ones; names starting `system.` are ignored."""
    live = {name: count for name, count in live.items() if not name.startswith("system.")}
    rows = [Row(name, count, live.get(name)) for name, count in expected.items()]
    rows += [Row(name, None, count) for name, count in live.items() if name not in expected]
    rows.sort(key=lambda row: (row.status == "EXTRA", row.name))
    return Comparison(tuple(rows))


def _number(value: int | None) -> str:
    return "none" if value is None else str(value)


def report_text(comparison: Comparison) -> str:
    """The plain, tab separated report: verdict, one line per collection, total."""
    lines = [f"verify {'PASS' if comparison.passed else 'FAIL'}"]
    lines += [
        f"{row.name}\t{_number(row.expected)}\t{_number(row.live)}\t{row.status}"
        for row in comparison.rows
    ]
    total = "ok" if comparison.passed else "DIFF"
    lines.append(f"total\t{comparison.expected_total}\t{comparison.live_total}\t{total}")
    return "\n".join(lines) + "\n"


def failure_message(comparison: Comparison, against: str) -> str:
    """One line naming how many collections differ, for a FAIL."""
    count = len(comparison.differences)
    noun = "collection differs" if count == 1 else "collections differ"
    return f"verify FAIL: {count} {noun} from {against} (see the report on stdout)"


def verify(settings: Settings) -> Comparison:
    """Compare live `JiraReposAnon` with the marker, print the report, raise on FAIL.

    Changes nothing, the marker included.
    """
    with mongo_errors("verify", secrets(settings)), root_client(settings) as client:
        marker = read_marker(client)
        if marker is None:
            raise MongoStoreError(
                f"no restore record for {DATABASE}: run `orgos mongo restore` first"
            )
        comparison = compare(marker.counts, live_counts(client))
    sys.stdout.write(report_text(comparison))
    if not comparison.passed:
        raise MongoStoreError(failure_message(comparison, "the restore record"))
    return comparison
