"""The findings report: the frozen rule for a real gap between the stores (spec 0014).

The rule was fixed by spec 0014, accepted and tagged `rule-frozen` before any
full run, and this module is pinned to it: an exact two sided paired sign flip
test on the per question differences (graph count minus SQL count), `real`
only at or below 1/20, compared on integers. The unit is the question, never
the run. The headline comes from three primary tests only (the noted series,
full form, one per question type); every other test is secondary and
descriptive, and the test on all questions is context. No line says the stores
are equal. Pure: the command reads the files and prints.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final, Literal

from orgos.eval.score import QUESTION_TYPES, STORE_NAMES

# The rule (AC-1 to AC-10), pinned by tests/test_eval_report.py to spec 0014.

ALPHA: Final = Fraction(1, 20)
# The fewest untied questions whose smallest reachable p is at or below ALPHA.
MIN_UNTIED: Final = 6
PRIMARY_COUNT: Final = 3

REAL: Final = "real, {store} ahead"
NOT_DETECTED: Final = (
    "graph scored higher on {k} of {n} questions; the test did not detect a gap (p = {p});"
    " this does not mean the stores are equal."
)
NO_RELIABLE: Final = "no reliable difference at this sample size"
PARTIAL: Final = "partial, no verdict"
COULD_NOT_REACH: Final = "could not reach 0.05 at this m"
NO_TEST: Final = "no questions, no test"
PRIMARY: Final = "primary"
SECONDARY: Final = "secondary, descriptive"
CONTEXT: Final = "context"
FAMILY_BOUND: Final = "at most 3 x 0.05 = 0.15"
CHANCE_ALL: Final = "at most {t} x 0.05"
NEED_UNTIED: Final = "a test needs at least 6 untied questions to ever read real"

RULE_LINES: Final = (
    "rule: an exact two sided paired sign flip test on the per question differences"
    " (graph count minus SQL count), counting all 2^n sign patterns, no sampling",
    "unit: the question; a question's count is its planned runs with the column true",
    "threshold: a gap is real only when p is at or below 1/20, compared on integers;"
    " the line then names the store ahead, by the sign of the sum",
    "primary family: the noted series, full form, one test per question type"
    " (aggregate, path_connection, mixed), each at 0.05, uncorrected; the headline comes"
    " only from these three",
    f"family bound: {FAMILY_BOUND}",
    "secondary, descriptive: the plain series and the second form; context: the test on"
    " all questions, with no verdict word",
    "a partial version or series prints its counts and p labelled partial, no verdict",
    "smallest reachable p at m untied questions: 2/2^m (1 when m is 0)",
)

# The reach table of spec 0014: m, the smallest p, the most questions that may go
# against the leader with equal gaps while the test still reads real, and p there.
REACH_TABLE: Final[tuple[tuple[int, Fraction, int | None, Fraction | None], ...]] = (
    (1, Fraction(1), None, None),
    (2, Fraction(1, 2), None, None),
    (3, Fraction(1, 4), None, None),
    (4, Fraction(1, 8), None, None),
    (5, Fraction(1, 16), None, None),
    (6, Fraction(1, 32), 0, Fraction(1, 32)),
    (7, Fraction(1, 64), 0, Fraction(1, 64)),
    (8, Fraction(1, 128), 0, Fraction(1, 128)),
    (9, Fraction(1, 256), 1, Fraction(5, 128)),
    (10, Fraction(1, 512), 1, Fraction(11, 512)),
    (11, Fraction(1, 1024), 1, Fraction(3, 256)),
    (12, Fraction(1, 2048), 2, Fraction(79, 2048)),
    (13, Fraction(1, 4096), 2, Fraction(23, 1024)),
    (14, Fraction(1, 8192), 2, Fraction(53, 4096)),
    (15, Fraction(1, 16384), 3, Fraction(9, 256)),
    (16, Fraction(1, 32768), 3, Fraction(697, 32768)),
    (17, Fraction(1, 65536), 4, Fraction(1607, 32768)),
    (18, Fraction(1, 131072), 4, Fraction(253, 8192)),
    (19, Fraction(1, 262144), 4, Fraction(1259, 65536)),
    (20, Fraction(1, 524288), 5, Fraction(5425, 131072)),
)

Role = Literal["primary", "secondary", "context"]
Direction = Literal["graph", "sql", "none"]


def sign_flip_p(differences: Sequence[int]) -> Fraction:
    """The exact two sided paired sign flip p of integer differences (AC-1).

    The share of the 2^n sign patterns whose absolute sum is at least the
    observed absolute sum, counted over the possible sums: no sampling, no
    float. A zero difference doubles every pattern, so it stays in the count
    without changing `p`.
    """
    ways: Counter[int] = Counter({0: 1})
    for difference in differences:
        following: Counter[int] = Counter()
        for total, count in ways.items():
            following[total + difference] += count
            following[total - difference] += count
        ways = following
    observed = abs(sum(differences))
    hits = sum(count for total, count in ways.items() if abs(total) >= observed)
    return Fraction(hits, 2 ** len(differences))


def smallest_p(m: int) -> Fraction:
    """The smallest `p` reachable with `m` untied questions: 2 / 2^m, or 1 when `m` is 0."""
    return Fraction(2, 2**m) if m else Fraction(1)


def is_real(p: Fraction) -> bool:
    """A gap is real only when `p` is at or below `ALPHA`, compared as fractions (AC-2)."""
    return p <= ALPHA


@dataclass(frozen=True)
class Verdict:
    """One test: its counts, `p`, reach, verdict and labels (AC-2 to AC-8)."""

    name: str
    role: Role
    differences: tuple[int, ...]
    partial: bool = False

    @property
    def n(self) -> int:
        """The questions in the test."""
        return len(self.differences)

    @property
    def m(self) -> int:
        """The questions with a non zero difference."""
        return sum(1 for difference in self.differences if difference)

    @property
    def graph_ahead(self) -> int:
        """The questions where the graph count is higher."""
        return sum(1 for difference in self.differences if difference > 0)

    @property
    def sql_ahead(self) -> int:
        """The questions where the SQL count is higher."""
        return sum(1 for difference in self.differences if difference < 0)

    @property
    def tied(self) -> int:
        """The questions with the same count in both stores."""
        return self.n - self.m

    @property
    def p(self) -> Fraction:
        """The exact two sided `p` of the differences."""
        return sign_flip_p(self.differences)

    @property
    def smallest_p(self) -> Fraction:
        """The smallest `p` reachable at this `m`."""
        return smallest_p(self.m)

    @property
    def direction(self) -> Direction:
        """The store ahead by the sign of the sum, `none` when the sum or `m` is 0."""
        total = sum(self.differences)
        if not self.m or not total:
            return "none"
        return "graph" if total > 0 else "sql"

    @property
    def real(self) -> bool:
        """`real` only for a test that is not context, not partial, and at or below `ALPHA`."""
        return self.role != "context" and not self.partial and is_real(self.p)

    def record(self) -> dict[str, object]:
        """The test as raw counts in the JSON file."""
        return {
            "name": self.name,
            "role": self.role,
            "n": self.n,
            "m": self.m,
            "graph_ahead": self.graph_ahead,
            "sql_ahead": self.sql_ahead,
            "tied": self.tied,
            "p": str(self.p),
            "smallest_p": str(self.smallest_p),
            "real": self.real,
            "direction": self.direction,
            "partial": self.partial,
            "differences": list(self.differences),
        }


def _label(role: Role) -> str:
    return {"primary": PRIMARY, "secondary": SECONDARY, "context": CONTEXT}[role]


def verdict_word(verdict: Verdict) -> str:
    """The verdict part of a line: real, the fixed sentence, no reliable difference, or partial."""
    if verdict.partial:
        return PARTIAL
    if verdict.role == "context":
        return f"differences {list(verdict.differences)}"
    if verdict.real:
        return REAL.format(store=STORE_NAMES[verdict.direction])
    if verdict.role == "primary":
        return NOT_DETECTED.format(k=verdict.graph_ahead, n=verdict.n, p=verdict.p)
    return NO_RELIABLE


def verdict_line(verdict: Verdict) -> str:
    """One test as printed: `n`, `m`, the sign counts, `p`, the reach, then the verdict (AC-6)."""
    head = f"{_label(verdict.role)}, {verdict.name}: "
    if not verdict.n:
        return head + NO_TEST
    reach = f"; {COULD_NOT_REACH}" if verdict.smallest_p > ALPHA else ""
    direction = STORE_NAMES.get(verdict.direction, "none")
    return (
        f"{head}n {verdict.n}, m {verdict.m}, graph ahead {verdict.graph_ahead}, SQL ahead"
        f" {verdict.sql_ahead}, tied {verdict.tied}, p = {verdict.p}, smallest p"
        f" {verdict.smallest_p}{reach}; direction {direction}. {verdict_word(verdict)}"
    )


# The reach table and `--rule` (AC-9, AC-10)


def reach_row(m: int) -> tuple[int, Fraction, int | None, Fraction | None]:
    """One reach table row computed by `sign_flip_p`: all gaps the same size, `k` against."""
    most: int | None = None
    at: Fraction | None = None
    for against in range(m):
        if against >= m - against:
            break
        p = sign_flip_p((1,) * (m - against) + (-1,) * against)
        if not is_real(p):
            break
        most, at = against, p
    return (m, smallest_p(m), most, at)


def reach_line(row: tuple[int, Fraction, int | None, Fraction | None]) -> str:
    """A reach table row as printed."""
    m, smallest, most, at = row
    against = "cannot read real" if most is None else str(most)
    return (
        f"m {m}: smallest p {smallest}; most against the leader, equal gaps: {against};"
        f" p there {'none' if at is None else at}"
    )


def rule_lines(type_counts: Mapping[str, int]) -> tuple[str, ...]:
    """What `orgos eval report --rule` prints; reads no run file, store or known answer."""
    per_type = ", ".join(f"{kind} {type_counts.get(kind, 0)}" for kind in QUESTION_TYPES)
    return (
        *RULE_LINES,
        "reach table (computed by sign_flip_p):",
        *(f"  {reach_line(reach_row(m))}" for m in range(1, 21)),
        f"questions per type: {per_type}",
        NEED_UNTIED,
    )
