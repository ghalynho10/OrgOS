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
from typing import Any, Final, Literal

from orgos.eval import EvalError
from orgos.eval.handoff import STORES
from orgos.eval.score import (
    LIMITS,
    NOT_REVIEWED,
    QUESTION_TYPES,
    STORE_NAMES,
    Entry,
    plain_statement,
    stale_line,
)

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


# The runs it reads (AC-11 to AC-19)

Series = Literal["noted", "plain"]
# The column each series is judged on (spec 0011 AC-23, spec 0013 AC-19).
COLUMNS: Final[Mapping[Series, str]] = {"noted": "correct", "plain": "answer_match"}
FORMS: Final = ("full form", "second form")
RUNS_FOLDER: Final = "results/agent_runs"


@dataclass(frozen=True)
class SeriesFiles:
    """One series of one version: the runs its `PLAN.json` plans, and its run files."""

    runs: int | None
    files: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class VersionFiles:
    """One version of `versions.jsonl`, its noted series, and its plain series when it has one."""

    version: str
    noted: SeriesFiles
    plain: SeriesFiles | None = None


def short(version: str) -> str:
    """The first 8 characters of a version hash, as the report prints it."""
    return version[:8]


def file_name(version: str, series: Series, data: Mapping[str, Any]) -> str:
    """The path of a run file, rebuilt from its own fields."""
    folder = f"{RUNS_FOLDER}/{version}" + ("/plain" if series == "plain" else "")
    return f"{folder}/{data['question']}-{data['store']}-{data['run']}.json"


def _series_of(version: VersionFiles) -> tuple[tuple[Series, SeriesFiles], ...]:
    noted: tuple[tuple[Series, SeriesFiles], ...] = (("noted", version.noted),)
    return noted if version.plain is None else (*noted, ("plain", version.plain))


COUNTED_FIELDS: Final = ("attempt_count", "retries", "attempts")


def check_fields(versions: Sequence[VersionFiles]) -> frozenset[str]:
    """Every run file holds `type` and `zero_answer`, the same across a question (AC-13).

    Returns the questions whose files carry `zero_answer` true; a missing field
    or a disagreement refuses, naming the file. A missing field is never false.
    Noted files also hold the fields the tables count (AC-20, AC-21), each attempt
    with its outcome, so a missing one refuses here and is never counted as 0.
    """
    seen: dict[str, tuple[str, object, object]] = {}
    for version in versions:
        for series, found in _series_of(version):
            required = ("type", "zero_answer", *(COUNTED_FIELDS if series == "noted" else ()))
            for data in found.files:
                name = file_name(version.version, series, data)
                for field in required:
                    if field not in data or data[field] is None:
                        raise EvalError(f"{name} holds no {field}; a missing field is never read")
                if series == "noted" and any("outcome" not in a for a in data["attempts"]):
                    raise EvalError(f"{name} holds an attempt with no outcome")
                question = str(data["question"])
                first = seen.setdefault(question, (name, data["type"], data["zero_answer"]))
                if (first[1], first[2]) != (data["type"], data["zero_answer"]):
                    field = "type" if first[1] != data["type"] else "zero_answer"
                    raise EvalError(f"{name} disagrees with {first[0]} on {field}")
    return frozenset(question for question, (_, _, zero) in seen.items() if zero is True)


def _key(data: Mapping[str, Any]) -> tuple[str, str, int]:
    return (str(data["question"]), str(data["store"]), int(data["run"]))


def planned_keys(question_ids: Sequence[str], runs: int) -> tuple[tuple[str, str, int], ...]:
    """Every planned run of a series: each question, both stores, run numbers 1 to `runs`."""
    return tuple(
        (question, store, number)
        for question in question_ids
        for store in STORES
        for number in range(1, runs + 1)
    )


def planned_files(
    found: SeriesFiles, question_ids: Sequence[str]
) -> tuple[dict[tuple[str, str, int], Mapping[str, Any]], tuple[tuple[str, str, int], ...]]:
    """The planned run files present, by key, and the planned keys with no file."""
    if found.runs is None:
        return {}, ()
    present = {_key(data): data for data in found.files}
    planned = planned_keys(question_ids, found.runs)
    return (
        {key: present[key] for key in planned if key in present},
        tuple(key for key in planned if key not in present),
    )


def is_complete(version: VersionFiles, question_ids: Sequence[str]) -> bool:
    """A plan that covers every frozen question in both stores, and every planned noted file.

    A `PLAN.json` always plans all the questions in both stores (`make_plan`), so a
    plan made with `--only` or `--store` leaves files missing and is not complete.
    """
    if version.noted.runs is None:
        return False
    return not planned_files(version.noted, question_ids)[1]


@dataclass(frozen=True)
class Choice:
    """The chosen version, whether it is partial, and the versions beside it (AC-16 to AC-18)."""

    chosen: VersionFiles
    partial: bool
    missing: tuple[str, ...]
    older: tuple[VersionFiles, ...]
    not_used: tuple[tuple[str, int], ...]


def require_versions(versions: Sequence[VersionFiles]) -> None:
    """With no version at all there is nothing to report: an `EvalError` (AC-18, AC-29)."""
    if not versions:
        raise EvalError(
            f"{RUNS_FOLDER}/versions.jsonl holds no version; there is nothing to report"
        )


def choose(versions: Sequence[VersionFiles], question_ids: Sequence[str]) -> Choice:
    """The last complete version in log order; else the newest, marked partial."""
    require_versions(versions)
    complete = [index for index, v in enumerate(versions) if is_complete(v, question_ids)]
    index = complete[-1] if complete else len(versions) - 1
    chosen = versions[index]
    missing: tuple[str, ...] = ()
    if not complete:
        if chosen.noted.runs is None:
            missing = (f"{RUNS_FOLDER}/{chosen.version}/PLAN.json",)
        else:
            missing = tuple(
                f"{RUNS_FOLDER}/{chosen.version}/{q}-{s}-{n}.json"
                for q, s, n in planned_files(chosen.noted, question_ids)[1]
            )
    return Choice(
        chosen=chosen,
        partial=not complete,
        missing=missing,
        older=tuple(versions[:index]),
        not_used=tuple((v.version, len(v.noted.files)) for v in versions[index + 1 :]),
    )


@dataclass(frozen=True)
class Paired:
    """One question in one test: its type, the graph and SQL counts and the planned runs."""

    question_id: str
    question_type: str
    graph: int
    sql: int
    runs: int

    @property
    def difference(self) -> int:
        """The graph count minus the SQL count."""
        return self.graph - self.sql


@dataclass(frozen=True)
class Pairing:
    """A series paired by question: the compared, the not compared and the null column count."""

    paired: tuple[Paired, ...]
    not_compared: tuple[tuple[str, str], ...]
    null_count: int

    def of(self, kind: str | None = None, drop: frozenset[str] = frozenset()) -> tuple[Paired, ...]:
        """The compared questions of one type (all when None), less the dropped ones."""
        return tuple(
            p
            for p in self.paired
            if (kind is None or p.question_type == kind) and p.question_id not in drop
        )


def pair(
    found: SeriesFiles,
    question_types: Mapping[str, str],
    column: str,
    load_id: str,
) -> Pairing:
    """Each question's count per store is its planned runs with `column` true (AC-11, AC-12).

    A question is compared only when both stores hold every planned run file and
    every file holds the current load ID. A null or absent column counts false.
    """
    present, _ = planned_files(found, tuple(question_types))
    paired: list[Paired] = []
    not_compared: list[tuple[str, str]] = []
    nulls = sum(1 for data in present.values() if data.get(column) is None)
    for question, kind in question_types.items():
        if found.runs is None:
            not_compared.append((question, "no PLAN.json"))
            continue
        keys = [key for key in planned_keys((question,), found.runs)]
        if any(key not in present for key in keys):
            not_compared.append((question, "missing files"))
            continue
        if any(present[key].get("load_id") != load_id for key in keys):
            not_compared.append((question, "other load ID"))
            continue
        counts = {
            store: sum(1 for key in keys if key[1] == store and present[key].get(column) is True)
            for store in STORES
        }
        paired.append(Paired(question, kind, counts["graph"], counts["sql"], found.runs))
    return Pairing(tuple(paired), tuple(not_compared), nulls)


# The tests (AC-4 to AC-8, AC-14, AC-25)


def form_tests(
    pairing: Pairing, series: Series, form: str, drop: frozenset[str], partial: bool
) -> tuple[Verdict, ...]:
    """One test per question type, then the all question context, for one series and form.

    Only the noted series in the full form is primary; every other test is secondary.
    """
    role: Role = "primary" if series == "noted" and form == FORMS[0] else "secondary"
    tests = tuple(
        Verdict(
            f"{series}, {form}, {kind}",
            role,
            tuple(p.difference for p in pairing.of(kind, drop)),
            partial,
        )
        for kind in QUESTION_TYPES
    )
    context = Verdict(
        f"{series}, {form}, all questions",
        "context",
        tuple(p.difference for p in pairing.of(None, drop)),
        partial,
    )
    return (*tests, context)


# The tables (AC-20 to AC-24, AC-26, AC-33)


def _present(found: SeriesFiles) -> dict[tuple[str, str, int], Mapping[str, Any]]:
    return {_key(data): data for data in found.files}


def _keys(
    found: SeriesFiles, question_ids: Sequence[str], store: str
) -> list[tuple[str, str, int]]:
    runs = found.runs or 0
    return [(q, store, n) for q in question_ids for n in range(1, runs + 1)]


def run_cell(
    found: SeriesFiles, question_ids: Sequence[str], store: str, column: str
) -> dict[str, Any]:
    """Raw counts over the planned runs of some questions in one store (AC-20, AC-21)."""
    present = _present(found)
    keys = _keys(found, question_ids, store)
    files = [present[key] for key in keys if key in present]
    return {
        column: sum(1 for data in files if data.get(column) is True),
        "total": len(keys),
        "missing": len(keys) - len(files),
        "attempts": sum(int(data["attempt_count"]) for data in files),
        "runs_with_a_retry": sum(1 for data in files if int(data["retries"]) > 0),
        "attempts_timed_out": sum(
            1 for data in files for a in data["attempts"] if a["outcome"] == "store_timeout"
        ),
        "attempts_at_row_cap": sum(
            1 for data in files for a in data["attempts"] if a["outcome"] == "row_cap"
        ),
        "limit_hit": {limit: sum(1 for d in files if d.get("limit") == limit) for limit in LIMITS},
    }


def gold_cell(gold: Sequence[Entry], question_ids: Sequence[str], store: str) -> dict[str, int]:
    """Gold pairs `correct` over the questions, from the gold summary's entries (AC-20)."""
    ids = set(question_ids)
    mine = [e for e in gold if e.store == store and e.question_id in ids]
    return {
        "correct": sum(1 for e in mine if e.outcome == "correct"),
        "total": len(ids),
        "correct_not_reviewed": sum(1 for e in mine if e.outcome == NOT_REVIEWED),
    }


def add_cells(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The sum of cells, nested counts included: the all question line (AC-20)."""
    total: dict[str, Any] = {}
    for cell in cells:
        for name, value in cell.items():
            if isinstance(value, Mapping):
                total[name] = add_cells([total.get(name, {}), value])
            else:
                total[name] = total.get(name, 0) + value
    return total


def pass_k(
    found: SeriesFiles, question_ids: Sequence[str], store: str, column: str
) -> dict[str, int]:
    """The questions with every planned run true, and with none true (AC-22)."""
    present = _present(found)
    every = none = 0
    for question in question_ids:
        values = [present.get(key) for key in _keys(found, (question,), store)]
        hits = [data is not None and data.get(column) is True for data in values]
        every += bool(values) and all(hits)
        none += not any(hits)
    return {"every_run": every, "no_run": none, "questions": len(question_ids)}


def source_counts(found: SeriesFiles, question_id: str, store: str) -> dict[str, int] | None:
    """The `missing` and `extra` counts summed over the planned runs (AC-23).

    None (`not recorded`) when a planned run has no file, no `steps` or an empty list.
    """
    present = _present(found)
    keys = _keys(found, (question_id,), store)
    files = [present.get(key) for key in keys]
    if not keys or any(data is None or not data.get("steps") for data in files):
        return None
    steps = [step for data in files if data is not None for step in data["steps"]]
    return {
        "missing": sum(int(step["missing"]) for step in steps),
        "extra": sum(int(step["extra"]) for step in steps),
    }


def no_answer_cell(found: SeriesFiles, question_ids: Sequence[str], store: str) -> dict[str, Any]:
    """Runs by `run_outcome` over the planned runs: `no_answer`, `limit_hit`, missing, no label.

    Descriptive, outside the rule (AC-33). Counts runs, not questions.
    """
    present = _present(found)
    keys = _keys(found, question_ids, store)
    cell: dict[str, Any] = {
        "no_answer": 0,
        "limit_hit": 0,
        "limit_hit_by_limit": dict.fromkeys(LIMITS, 0),
        "missing": 0,
        "no_label": 0,
        "total": len(keys),
    }
    for key in keys:
        data = present.get(key)
        if data is None:
            cell["missing"] += 1
            continue
        outcome = data.get("run_outcome")
        if outcome is None:
            cell["no_label"] += 1
        elif outcome == "no_answer":
            cell["no_answer"] += 1
        elif outcome == "limit_hit":
            cell["limit_hit"] += 1
            if data.get("limit") in LIMITS:
                cell["limit_hit_by_limit"][data["limit"]] += 1
    return cell


# The version comparison (AC-17)

V1: Final = "2627493a6db977e743449ecfe577e2d28af69815d535fd1e8664ba584734a618"
V2: Final = "cae2a6746caf3d715a7b065d3abdc5b7f3abcce5e9df0620956827860e03c48e"
Q09: Final = "Q09"
VERSION_SENTENCE: Final = (
    "Agent versions 2627493a and cae2a674: source scores are not compared, because only"
    " cae2a674 was told the evidence layout."
)
VERSION_HEADER: Final = (
    "version comparison, noted series, full form, answer_match k/n side by side"
    " (Q09 is not in this type total)"
)


def _k_n(paired: Sequence[Paired], store: str) -> dict[str, int]:
    return {
        "answer_match": sum(p.graph if store == "graph" else p.sql for p in paired),
        "total": sum(p.runs for p in paired),
    }


def version_comparison(
    versions: Sequence[VersionFiles], question_types: Mapping[str, str], load_id: str
) -> dict[str, Any]:
    """`answer_match` counts of `V1` and `V2` side by side, Q09 on its own line (AC-17).

    A question joins only when it is compared under AC-11 and AC-12 in both versions.
    """
    found = {v.version: v for v in versions}
    absent = [short(v) for v in (V1, V2) if v not in found]
    if absent:
        return {"left_out": f"{', '.join(absent)} not in {RUNS_FOLDER}/versions.jsonl"}
    pairings = {v: pair(found[v].noted, question_types, "answer_match", load_id) for v in (V1, V2)}
    compared = {v: {p.question_id: p for p in pairings[v].paired} for v in (V1, V2)}
    reasons = {v: dict(pairings[v].not_compared) for v in (V1, V2)}
    joined = [q for q in question_types if all(q in compared[v] for v in (V1, V2))]
    not_compared = {
        q: ", ".join(f"{reasons[v][q]} in {short(v)}" for v in (V1, V2) if q in reasons[v])
        for q in question_types
        if q not in joined
    }
    ids = tuple(question_types)
    cells = {
        kind: {
            store: {
                short(v): _k_n(
                    [compared[v][q] for q in joined if q != Q09 and question_types[q] == kind],
                    store,
                )
                for v in (V1, V2)
            }
            for store in STORES
        }
        for kind in QUESTION_TYPES
    }
    q09: dict[str, Any] = (
        {store: {short(v): _k_n([compared[v][Q09]], store) for v in (V1, V2)} for store in STORES}
        if Q09 in joined
        else {"not_compared": not_compared.get(Q09, "not a question")}
    )
    return {
        "versions": [V1, V2],
        "partial": {short(v): not is_complete(found[v], ids) for v in (V1, V2)},
        "cells": cells,
        "q09": q09,
        "not_compared": not_compared,
    }


def version_comparison_lines(block: Mapping[str, Any]) -> list[str]:
    """The block as printed, the fixed sentence directly under it, or why it is left out."""
    if "left_out" in block:
        return [f"version comparison left out: {block['left_out']}"]

    def shown(by_version: Mapping[str, Mapping[str, int]]) -> str:
        return ", ".join(
            f"{name}{' (partial)' if block['partial'][name] else ''}"
            f" {cell['answer_match']}/{cell['total']}"
            for name, cell in by_version.items()
        )

    lines = [VERSION_HEADER]
    for kind, by_store in block["cells"].items():
        lines.extend(
            f"  {kind}, {STORE_NAMES[store]}: {shown(by_store[store])}" for store in STORES
        )
    q09 = block["q09"]
    if "not_compared" in q09:
        lines.append(f"  {Q09}: not compared ({q09['not_compared']})")
    else:
        lines.extend(f"  {Q09}, {STORE_NAMES[store]}: {shown(q09[store])}" for store in STORES)
    if block["not_compared"]:
        lines.append(
            "  not compared in both versions: "
            + ", ".join(f"{q} ({reason})" for q, reason in block["not_compared"].items())
        )
    lines.append(VERSION_SENTENCE)
    return lines


# The limitation lines (AC-30, AC-34)


@dataclass(frozen=True)
class Limitation:
    """One fixed limitation line: its group, id, source and text."""

    group: int
    id: str
    source: str
    text: str


# Groups 1 to 4: the texts are frozen by `rule-frozen`; the ids are assigned in spec order.
LIMITATIONS: Final[tuple[Limitation, ...]] = (
    Limitation(
        1,
        "G1-1",
        "scope feature 17, Done when",
        "known answers and dbt share one reading of the Jira fields",
    ),
    Limitation(
        1,
        "G1-2",
        "scope feature 17, Done when",
        (
            "the gold queries were model drafted blind to the answers, Q01 as a teaching pass, "
            "with counts only feedback, and Q01's pattern shaped the rest"
        ),
    ),
    Limitation(
        1,
        "G1-3",
        "scope feature 17, Done when",
        "`model_int` is written in SQL, so the graph may lean relational.",
    ),
    Limitation(
        2,
        "G2-1",
        "spec 0014 AC-4, AC-6 and the reach table",
        (
            "n is 20, with 6 or 7 questions per type, so a test with fewer than 6 untied "
            "questions can never read `real`, and `did not detect a gap` is not `equal`"
        ),
    ),
    Limitation(
        2,
        "G2-2",
        "spec 0014 AC-4 and AC-7",
        "the family bound of AC-4 and the chance line of AC-7",
    ),
    Limitation(
        2,
        "G2-3",
        (
            "spec 0014 rationale, Context (the literature look back, no longer in "
            "docs/session-notes.md)"
        ),
        (
            "the known answer code, the gold queries and the agent come from one model family, "
            "a shared blind spot and not a bias between stores"
        ),
    ),
    Limitation(
        2,
        "G2-4",
        "spec 0014 rationale, Context; spec 0013 References (BIRD execution accuracy)",
        "the strict score is all or nothing and not comparable to published execution accuracy",
    ),
    Limitation(
        2,
        "G2-5",
        "spec 0011 AC-24",
        (
            "query and run times are recorded but never compared or ranked, because both stores"
            " run untuned in Docker on one laptop on a small dataset (spec 0011 AC-24)"
        ),
    ),
    Limitation(
        2,
        "G2-6",
        "spec 0013 Follow-up",
        (
            "every scored run got the reading note, and plain runs are a lower bound that still"
            " carried shape, unit and hop limit (spec 0013 Follow-up)."
        ),
    ),
    Limitation(
        3,
        "G3-1",
        (
            "eval/data-description.md, Negative time entries (the 70 entries); "
            "docs/session-notes.md, Ruled out (about 1.5%); the CLOV 12% is in no file read "
            "(spec 0014 Follow-up)"
        ),
        (
            "hours count positive entries only, 70 negative entries left out (about 1.5% of "
            "counted seconds, CLOV about 12%)"
        ),
    ),
    Limitation(
        3,
        "G3-2",
        "docs/session-notes.md, Q03 zero and Q16 zero; spec 0014 AC-14",
        "Q03 and Q16 are zero answers, shown both ways",
    ),
    Limitation(
        3,
        "G3-3",
        "scope, feature 17 candidates; not confirmed from any file read (spec 0014 Follow-up)",
        "Q08 is one hop",
    ),
    Limitation(
        3,
        "G3-4",
        "spec 0012 AC-7",
        (
            "Q10 is scored through its sources, and was told to return entities, not pairs, in "
            "both stores"
        ),
    ),
    Limitation(
        3,
        "G3-5",
        "spec 0008",
        "categories are properties, not labels (spec 0008)",
    ),
    Limitation(
        3,
        "G3-6",
        "spec 0012 AC-17, AC-18",
        (
            "Q11 mixes people and projects in one set, and the sources of an empty, zero or "
            "false answer are `[]` (spec 0012 AC-17, AC-18)"
        ),
    ),
    Limitation(
        3,
        "G3-7",
        "spec 0011 Follow-up",
        "the literal check misses small numbers (spec 0011 Follow-up).",
    ),
    Limitation(
        4,
        "G4-1",
        "spec 0012 Follow-up",
        "the reading note's definitions and the list of source kinds were given",
    ),
    Limitation(
        4,
        "G4-2",
        "spec 0012 Follow-up",
        (
            "temperature, `top_p` and `top_k` were the provider default and thinking was "
            "adaptive at effort low"
        ),
    ),
    Limitation(
        4,
        "G4-3",
        "spec 0012 Follow-up",
        "a run's label follows spec 0011 AC-10 as amended",
    ),
    Limitation(
        4,
        "G4-4",
        "spec 0012 Follow-up",
        "cap hits, timeouts and `limit_hit` per limit are shown beside every headline",
    ),
    Limitation(
        4,
        "G4-5",
        "spec 0012 Follow-up",
        "the agent was given the answer shape and unit of each question",
    ),
    Limitation(
        4,
        "G4-6",
        "spec 0012 Follow-up",
        "the 400,000 token limit was chosen so the 15 call limit binds first",
    ),
    Limitation(
        4,
        "G4-7",
        "spec 0012 Follow-up",
        (
            "and if a real bug is found in the agent when no agent version is left (the cap is "
            "three), the runs are reported as they are and the bug is named here."
        ),
    ),
    Limitation(
        5,
        "G5-1",
        (
            'spec 0015 Consequences and Follow-up, 2026-11-13; `docs/session-notes.md`, "How '
            'others do it", 2026-11-13'
        ),
        (
            "From agent version 2 the agent was told the evidence layout of each scored "
            "question, which version 1 had to infer, so version 1 and version 2 source scores "
            "are not compared, and the move from version 1 to version 2 is reported as a format"
            " change (spec 0015 changes format only)."
        ),
    ),
    Limitation(
        5,
        "G5-2",
        "spec 0015 Consequences, 2026-11-13",
        (
            "Noted runs can get a layout send back that plain runs cannot, because plain has no"
            " layout line; send backs appear in the log lines and are not recorded in run "
            "files."
        ),
    ),
    Limitation(
        5,
        "G5-3",
        "spec 0015 Consequences and Follow-up, 2026-11-13",
        (
            "Change C4 applies to Q09 only, with identical text in both stores; version 1 and "
            "version 2 Q09 counts are shown on their own line."
        ),
    ),
    Limitation(
        5,
        "G5-4",
        "spec 0015 Consequences and Follow-up, 2026-11-13",
        (
            "The layout check is soft, so a layout miss shows as `source_match` false and never"
            " as a lost answer by the layout alone; a send back can still lead the model into a"
            " structural slip, so the `no_answer` count of each version is printed (AC-33); a "
            "send back also spends model requests, so a run can end as `limit_hit` because of "
            "the layout check, and the `limit_hit` count of each version is printed beside it."
        ),
    ),
    Limitation(
        5,
        "G5-5",
        "spec 0015 Consequences, 2026-11-13, added after its review",
        (
            'Two sentences of the shared Sources text, "Each kind may point at its own result."'
            ' and "A source column holds no NULL, so query a kind separately when some rows '
            'lack it.", tell the agent how to shape its result tables, with identical text for '
            "every question and both stores; spec 0015 says they edge toward method."
        ),
    ),
    Limitation(
        5,
        "G5-6",
        (
            "spec 0013 Consequences, 2026-11-11, and its Follow-up (undated); spec 0015 "
            "`index.md` line 100, 2026-11-13"
        ),
        (
            "The shared instruction text still speaks of a reading note in plain runs, in both "
            'stores, so the plain series is called "without the reading note", never "cold".'
        ),
    ),
    Limitation(
        5,
        "G5-7",
        "`docs/session-notes.md`, Q03 zero, 2026-11-11",
        (
            "Q03 is a zero answer with empty evidence, not a real zero, so a query that finds "
            "nothing scores correct and the zero says nothing about lateness; the stage reading"
            " is in the note."
        ),
    ),
    Limitation(
        5,
        "G5-8",
        "`docs/session-notes.md`, Q13 limitation, 2026-11-12",
        (
            "Q13 is scored as which sub-tasks of one issue (shape set, frozen); the score "
            "checks the sub-task IDs and, in the sources, every positive time entry on them, "
            "and does not check the summed hours, the assignee or the status."
        ),
    ),
    Limitation(
        5,
        "G5-9",
        "`docs/session-notes.md`, Q16 zero, 2026-11-13",
        (
            "Q16 is a real zero but a weak test: part of its filter never removes anything, so "
            "a query that gets that part wrong still scores correct; the stage reading is in "
            "the note."
        ),
    ),
    Limitation(
        5,
        "G5-10",
        (
            '`docs/OrgOS-carry-forward.md`, "Findings report (feature 17), additions", file '
            "dated 2026-11-03"
        ),
        (
            "`eval/data-description.md`, already published, holds real looking names and emails"
            " taken from changelog display strings of the public CC BY 4.0 dataset, and "
            "`tests/fixtures/corpus/` possibly does too (not checked); they are not newly "
            "exposed and the file is not edited."
        ),
    ),
    Limitation(
        5,
        "G5-11",
        (
            "`docs/OrgOS-carry-forward.md`, that section and its date; this spec's "
            "Consequences, 2026-11-11"
        ),
        "The accuracy numbers hold only for this dataset, one model family and one load.",
    ),
    Limitation(
        5,
        "G5-12",
        "the `time` field of the run files, the git tag `rule-frozen`, checked 2026-11-14",
        (
            "The commit tagged `rule-frozen` is dated 2026-11-11 at 23:57 UTC. The six version "
            "1 Q01 runs (2026-11-11, 16:46 to 17:02 UTC) came before it; every other agent run "
            "came after it (version 1 from 2026-11-12, version 2 from 2026-11-13, the plain "
            "series from 2026-11-14). Replay files are not agent results."
        ),
    ),
    Limitation(
        5,
        "G5-13",
        "this amendment, 2026-11-14; spec 0015 commit `20d2486`, 2026-11-13 20:59 UTC",
        (
            "The measure of the version 1 against version 2 block (`answer_match`, Q09 apart) "
            "and the `no_answer` cells were set on 2026-11-14, after the version 1, version 2 "
            "and plain results had been seen; the measure follows spec 0015 line 165, written "
            "before the first version 2 run. The first form of AC-17 (a disagreement line) was "
            "replaced on 2026-11-14, also after the results were seen, by side by side counts, "
            "justified only by the cross check's list of decisions the disagreement rule "
            "needed."
        ),
    ),
    Limitation(
        5,
        "G5-14",
        'spec 0015 `rationale.md` "How others do it" (SWE-bench row), 2026-11-13',
        (
            "The layout line reveals the structure of the evidence grading, which is the kinds "
            "and the step count, never IDs."
        ),
    ),
    Limitation(
        5,
        "G5-15",
        'spec 0015 `rationale.md` "How others do it", last row, 2026-11-13',
        (
            "The layout line is new: no benchmark read tells the model which record kinds to "
            "cite per question."
        ),
    ),
)


def limitation_lines() -> list[str]:
    """The fixed limitation lines as printed: id, source in brackets, text (AC-30)."""
    return [f"{line.id} [{line.source}]: {line.text}" for line in LIMITATIONS]


# The whole report (AC-15 to AC-34)

SCHEMA_VERSION: Final = 1
LABEL_DESCRIPTIVE: Final = "descriptive, outside the rule"
LOWER_BOUND: Final = "lower bound"
UNTESTED: Final = "untested"
DIAGNOSTIC: Final = "diagnostic"
NOT_RECORDED: Final = "not recorded"


@dataclass(frozen=True)
class Report:
    """The printed lines and the JSON record of one report, the same cells in both."""

    lines: tuple[str, ...]
    record: Mapping[str, Any]


def _fraction(cell: Mapping[str, Any], key: str) -> str:
    return f"{cell[key]}/{cell['total']}"


def _beside(cell: Mapping[str, Any]) -> str:
    limits = ", ".join(f"{name} {count}" for name, count in cell["limit_hit"].items())
    return (
        f"attempts timed out {cell['attempts_timed_out']}, at the row cap"
        f" {cell['attempts_at_row_cap']}, limit_hit {limits}"
    )


def _forms(
    question_types: Mapping[str, str], zero: frozenset[str]
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Per form and type, the question ids; the second form drops the zero answer questions."""
    return {
        form: {
            kind: tuple(
                q
                for q, k in question_types.items()
                if k == kind and (form == FORMS[0] or q not in zero)
            )
            for kind in QUESTION_TYPES
        }
        for form in FORMS
    }


def _agent_tables(
    found: SeriesFiles,
    forms: Mapping[str, Mapping[str, tuple[str, ...]]],
    gold: Sequence[Entry] | None,
) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for form, by_type in forms.items():
        table: dict[str, Any] = {}
        for store in STORES:
            cells = {
                kind: {"agent": run_cell(found, ids, store, "correct")}
                for kind, ids in by_type.items()
            }
            if gold is not None:
                for kind, ids in by_type.items():
                    cells[kind]["gold"] = gold_cell(gold, ids, store)
            cells["all questions"] = {
                part: add_cells([cells[kind][part] for kind in QUESTION_TYPES])
                for part in cells[QUESTION_TYPES[0]]
            }
            table[store] = cells
        tables[form] = table
    return tables


def _table_lines(title: str, tables: Mapping[str, Any]) -> list[str]:
    lines = [title]
    for form, table in tables.items():
        lines.append(f"  {form}:")
        for store, cells in table.items():
            for kind, cell in cells.items():
                agent = cell["agent"]
                gold = (
                    f"gold {_fraction(cell['gold'], 'correct')} correct (correct, not"
                    f" reviewed {cell['gold']['correct_not_reviewed']}); "
                    if "gold" in cell
                    else ""
                )
                missing = f"; missing {agent['missing']} run files" if agent["missing"] else ""
                lines.append(
                    f"    {kind}, {STORE_NAMES[store]}: {gold}agent"
                    f" {_fraction(agent, 'correct')} correct, attempts {agent['attempts']},"
                    f" runs with a retry {agent['runs_with_a_retry']}; beside it:"
                    f" {_beside(agent)}{missing}"
                )
    return lines


def _plain_status(
    choice: Choice,
    versions: Sequence[VersionFiles],
    question_ids: Sequence[str],
    load_id: str,
) -> tuple[str | None, list[str]]:
    """Why the plain series is left out (None when it joins), and the stale lines (AC-19)."""
    plain = choice.chosen.plain
    order = [v.version for v in versions]
    if plain is None:
        others = [v for v in versions if v.plain is not None]
        if not others:
            return "no plain series", []
        stale = [
            stale_line(v.version, order[order.index(v.version) + 1 :])
            for v in others
            if order.index(v.version) < order.index(choice.chosen.version)
        ]
        shown = ", ".join(short(v.version) for v in others)
        return f"other version (the plain series is under {shown})", stale
    if plain.runs is None:
        return "no plain PLAN.json", []
    present, missing = planned_files(plain, question_ids)
    if missing:
        return f"missing files ({len(missing)} planned plain run files)", []
    if any(data.get("load_id") != load_id for data in present.values()):
        return "other load ID", []
    return None, []


Section = tuple[list[str], Any]
SeriesRow = tuple[Series, SeriesFiles, bool]


def _tests_section(
    series: Sequence[SeriesRow],
    pairings: Mapping[Series, Pairing],
    zero: frozenset[str],
) -> Section:
    tests = {
        form: [
            verdict
            for name, _, partial in series
            for verdict in form_tests(
                pairings[name], name, form, zero if form == FORMS[1] else frozenset(), partial
            )
        ]
        for form in FORMS
    }
    count = sum(1 for form in FORMS for v in tests[form] if v.role != "context")
    lines = ["the rule (spec 0014, frozen by rule-frozen); the headline is the primary tests only"]
    for form in FORMS:
        lines.append(f"  {form}:")
        lines.extend(f"    {verdict_line(v)}" for v in tests[form])
        lines.append(f"    chance, primary family: {FAMILY_BOUND}")
        lines.append(f"    chance, all {count} type tests: {CHANCE_ALL.format(t=count)}")
    for name, _, _ in series:
        pairing = pairings[name]
        lines.append(
            f"  {name}: {pairing.null_count} run files with {COLUMNS[name]} null or absent,"
            " counted false"
        )
        lines.append(
            f"  {name}, not compared: "
            + (", ".join(f"{q} ({reason})" for q, reason in pairing.not_compared) or "none")
        )
    record = {
        "tests": {form: [v.record() for v in tests[form]] for form in FORMS},
        "chance": {"primary_family": FAMILY_BOUND, "all_type_tests": CHANCE_ALL.format(t=count)},
        "null_counts": {name: pairings[name].null_count for name, _, _ in series},
        "not_compared": {name: dict(pairings[name].not_compared) for name, _, _ in series},
    }
    return lines, record


def _pass_k_section(series: Sequence[SeriesRow], by_type: Mapping[str, tuple[str, ...]]) -> Section:
    cells = {
        name: {
            store: {
                kind: pass_k(found, by_type[kind], store, COLUMNS[name]) for kind in QUESTION_TYPES
            }
            for store in STORES
        }
        for name, found, _ in series
    }
    lines = ["pass^k, outside the rule: questions with every planned run true, with none true"]
    for name, by_store in cells.items():
        for store, by_kind in by_store.items():
            for kind, cell in by_kind.items():
                lines.append(
                    f"  {name}, {STORE_NAMES[store]}, {kind}: every run {cell['every_run']}"
                    f" of {cell['questions']}, no run {cell['no_run']} of {cell['questions']}"
                )
    return lines, cells


def _diagnostic_section(found: SeriesFiles, by_type: Mapping[str, tuple[str, ...]]) -> Section:
    ids = [q for kind in QUESTION_TYPES for q in by_type[kind]]
    questions = {
        store: {q: source_counts(found, q, store) for q in sorted(ids)} for store in STORES
    }
    lines = [f"source {DIAGNOSTIC}, noted series: missing and extra summed over the planned runs"]
    for store, by_question in questions.items():
        for q, counts in by_question.items():
            shown = (
                NOT_RECORDED
                if counts is None
                else f"missing {counts['missing']}, extra {counts['extra']}"
            )
            lines.append(f"  {STORE_NAMES[store]}, {q}: {shown}")
    types: dict[str, dict[str, Any]] = {}
    for store in STORES:
        types[store] = {}
        for kind in QUESTION_TYPES:
            recorded = [c for q in by_type[kind] if (c := questions[store][q]) is not None]
            missing = [q for q in by_type[kind] if questions[store][q] is None]
            summed = add_cells(recorded) or {"missing": 0, "extra": 0}
            types[store][kind] = {"counts": summed, "not_recorded": missing}
            lines.append(
                f"  {STORE_NAMES[store]}, {kind}: missing {summed['missing']}, extra"
                f" {summed['extra']}; {NOT_RECORDED}: {', '.join(missing) or 'none'}"
            )
    return lines, {"questions": questions, "types": types}


def _plain_section(
    chosen: VersionFiles,
    order: Sequence[str],
    by_type: Mapping[str, tuple[str, ...]],
    zero: frozenset[str],
) -> Section:
    plain = chosen.plain
    assert plain is not None

    def stores(question_ids: Sequence[str]) -> list[dict[str, Any]]:
        made = plain_statement(
            chosen.noted.files,
            plain.files,
            (),
            version=chosen.version,
            question_ids=question_ids,
            runs=plain.runs or 0,
            zero_guarded=zero,
            versions=order,
        )
        return list(made["stores"])

    beside = {kind: stores(by_type[kind]) for kind in QUESTION_TYPES}
    lines = [f"noted beside plain, answer_match; plain is a {LOWER_BOUND}"]
    for kind, parts in beside.items():
        for part in parts:
            full, second = part["full"], part["without_zero_guarded"]
            line = (
                f"  {kind}, {STORE_NAMES[part['store']]}: noted"
                f" {_fraction(full['noted'], 'answer_match')}, plain"
                f" {_fraction(full['plain'], 'answer_match')} ({LOWER_BOUND}); second form"
                f" noted {_fraction(second['noted'], 'answer_match')}, plain"
                f" {_fraction(second['plain'], 'answer_match')}"
            )
            if part["not_compared"]:
                line += "; not compared: " + ", ".join(
                    f"{q} ({reason})" for q, reason in part["not_compared"].items()
                )
            lines.append(line)
    every = [q for kind in QUESTION_TYPES for q in by_type[kind]]
    drop = {part["store"]: part["full"] for part in stores(sorted(every))}
    for store, full in drop.items():
        lines.append(
            f"  drop from noted to plain, {STORE_NAMES[store]}, {LOWER_BOUND}, {UNTESTED}:"
            f" noted {_fraction(full['noted'], 'answer_match')}, plain"
            f" {_fraction(full['plain'], 'answer_match')} answer_match"
        )
    return lines, {"noted_beside_plain": beside, "drop": drop}


def _older_section(
    older: Sequence[VersionFiles],
    forms: Mapping[str, Mapping[str, tuple[str, ...]]],
    ids: Sequence[str],
) -> Section:
    lines: list[str] = []
    record: dict[str, Any] = {}
    for version in older:
        name = short(version.version)
        if version.noted.runs is None:
            record[version.version] = {"left_out": "no PLAN.json"}
            lines.append(f"older version {name}: no PLAN.json, no counts")
            continue
        record[version.version] = _agent_tables(version.noted, forms, None)
        partial = "" if is_complete(version, ids) else ", partial"
        lines.extend(
            _table_lines(
                f"older version {name}{partial}, noted series, raw counts",
                record[version.version],
            )
        )
    return lines, record


def _no_answer_section(shown: Sequence[VersionFiles], ids: Sequence[str]) -> Section:
    lines = [f"no_answer cells, noted series, {LABEL_DESCRIPTIVE}: runs over the planned runs"]
    record: dict[str, Any] = {}
    for version in shown:
        name = short(version.version) + ("" if is_complete(version, ids) else ", partial")
        if version.noted.runs is None:
            record[version.version] = {"left_out": "no PLAN.json"}
            lines.append(f"  {name}: no PLAN.json, no counts")
            continue
        cells = {store: no_answer_cell(version.noted, ids, store) for store in STORES}
        record[version.version] = cells
        for store, cell in cells.items():
            limits = ", ".join(f"{k} {v}" for k, v in cell["limit_hit_by_limit"].items())
            lines.append(
                f"  {name}, {STORE_NAMES[store]}: no_answer {cell['no_answer']},"
                f" limit_hit {cell['limit_hit']} ({limits}), missing {cell['missing']},"
                f" no label {cell['no_label']}, of {cell['total']} planned runs"
            )
    return lines, record


def findings(
    versions: Sequence[VersionFiles],
    question_types: Mapping[str, str],
    load_id: str,
    gold: Sequence[Entry],
) -> Report:
    """The findings report from the read files: tests, tables and limitations, counts only.

    `versions` are in `versions.jsonl` order, `question_types` the frozen questions as
    `read_questions` returns them, `gold` the gold summary's entries. Prints no
    conclusion and no recommendation (AC-28), and no answer value (AC-32).
    """
    ids = tuple(question_types)
    order = [v.version for v in versions]
    zero = check_fields(versions)
    choice = choose(versions, ids)
    chosen = choice.chosen
    forms = _forms(question_types, zero)

    lines = [
        f"findings report, version {short(chosen.version)}"
        + (", partial" if choice.partial else "")
        + f", load ID {load_id}",
    ]
    if choice.partial:
        lines.append(f"  partial, no complete version; missing: {', '.join(choice.missing)}")
    lines.extend(
        f"  version {short(v)} is newer and not complete ({count} run files); not used"
        for v, count in choice.not_used
    )
    lines.append(
        "  second form: without the zero answer questions " + (", ".join(sorted(zero)) or "none")
    )

    plain_reason, stale = _plain_status(choice, versions, ids, load_id)
    series: list[SeriesRow] = [("noted", chosen.noted, choice.partial)]
    if plain_reason is None and chosen.plain is not None:
        series.append(("plain", chosen.plain, choice.partial))
    pairings = {
        name: pair(found, question_types, COLUMNS[name], load_id) for name, found, _ in series
    }
    tests_lines, tests = _tests_section(series, pairings, zero)
    tables = _agent_tables(chosen.noted, forms, gold)
    table_lines = _table_lines(
        f"tables, version {short(chosen.version)}, noted series, raw counts", tables
    )
    pass_lines, pass_cells = _pass_k_section(series, forms[FORMS[0]])
    diagnostic_lines, diagnostic = _diagnostic_section(chosen.noted, forms[FORMS[0]])
    plain_lines: list[str] = [f"the plain tables are left out: {plain_reason}", *stale]
    plain: dict[str, Any] = {"noted_beside_plain": None, "drop": None}
    if plain_reason is None:
        plain_lines, plain = _plain_section(chosen, order, forms[FORMS[0]], zero)
    older_lines, older = _older_section(choice.older, forms, ids)
    comparison = version_comparison(versions, question_types, load_id)
    no_answer_lines, no_answer = _no_answer_section((*choice.older, chosen), ids)

    for section in (
        tests_lines,
        table_lines,
        pass_lines,
        diagnostic_lines,
        plain_lines,
        older_lines,
        version_comparison_lines(comparison),
        no_answer_lines,
        ["limitations", *(f"  {line}" for line in limitation_lines())],
    ):
        if section:
            lines.extend(("", *section))

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "version": chosen.version,
        "partial": choice.partial,
        "missing": list(choice.missing),
        "not_used": dict(choice.not_used),
        "load_id": load_id,
        "zero_answer_questions": sorted(zero),
        **tests,
        "tables": tables,
        "pass_k": pass_cells,
        "source_diagnostic": diagnostic,
        "plain": {"left_out": plain_reason, "stale": stale, **plain},
        "older_versions": older,
        "version_comparison": {k: v for k, v in comparison.items() if k != "q09"},
        "version_comparison_q09": comparison.get("q09"),
        "version_comparison_sentence": None if "left_out" in comparison else VERSION_SENTENCE,
        "no_answer_cells": no_answer,
        "limitations": [
            {"group": line.group, "id": line.id, "source": line.source, "text": line.text}
            for line in LIMITATIONS
        ],
    }
    return Report(tuple(lines), record)
