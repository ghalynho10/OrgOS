"""Gold queries: the files of a pair, one try through both stores' executors, and `gold decide`.

Spec 0011 AC-11 to AC-18 and AC-21. A pair is one question in one store: an
answer file, one sources file per step of the known answer's source list, one
stage file per numbered source step of the reading note when the question is
zero guarded, and an explain file. `gold try` runs the whole pair, reshapes the
rows with the question's adapter, scores them with `store_answer` and
`compare`, logs the try, and prints three booleans and per step counts. It
never prints an answer key, value or ID, the store's or the known one; the one
exception is a zero guarded question's stage counts, the store's own counts.
Known answers are read here by code and never printed or logged.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import ValidationError

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.answers import read_questions
from orgos.eval.frozen import ANSWERS, sha256_file
from orgos.eval.gold_adapters import ADAPTERS, Adapter
from orgos.eval.handoff import STORES, ScoredOutcome, Store, StoreOutage
from orgos.eval.literal import literal_problems, mask
from orgos.eval.models import AnswerFile, Question
from orgos.eval.preflight import (
    preflight,
    record_load_id,
    require_frozen_set,
    write_fingerprint,
)
from orgos.eval.score import (
    NOT_REVIEWED,
    Entry,
    Scored,
    SummaryOutcome,
    failed,
    score,
    summary_lines,
)
from orgos.eval.tries import (
    MAX_FAILED,
    PairState,
    append,
    citation_problem,
    git_record,
    latest_correct,
    load_id_problems,
    now,
    pair_state,
    read_tries,
    reading_steps,
    schema_names,
)
from orgos.graphstore.mapping import load_mapping
from orgos.graphstore.query import (
    GraphConnectionLost,
    GraphQueryError,
    GraphQueryTimeout,
    GraphRowCapError,
    reader_driver,
    run_cypher,
)
from orgos.sqlstore.query import (
    QueryResult,
    SqlConnectionLost,
    SqlQueryError,
    SqlQueryTimeout,
    SqlRowCapError,
    reader_connection,
    run_sql,
)

GOLD = Path("gold")
# The pin: every accepted pair's query and explain files, in `sha256sum` format (AC-19, AC-20).
ACCEPTED = GOLD / "ACCEPTED"
FOLDERS: Mapping[Store, str] = {"sql": "sql", "graph": "cypher"}
COMPARE_CODE = Path("src/orgos/eval/compare.py")

FileOutcome = Literal["ok", "store_timeout", "row_cap", "error"]


def parse_store(text: str) -> Store:
    """`sql` or `graph`, or an `EvalError` naming both."""
    for store in STORES:
        if text == store:
            return store
    raise EvalError(f"--store must be sql or graph, not {text!r}")


def question_by_id(root: Path, question_id: str) -> Question:
    """The frozen question with its amendments in place, or an `EvalError`."""
    question = next((q for q in read_questions(root) if q.id == question_id), None)
    if question is None:
        raise EvalError(f"{question_id} is not a question of the eval set")
    return question


def known_answers(root: Path) -> Mapping[str, Mapping[str, Any]]:
    """Every known answer in the JSON form `compare` takes; read by code, never printed."""
    try:
        data = json.loads((root / ANSWERS).read_text(encoding="utf-8"))
        AnswerFile.model_validate(data)
    except OSError as error:
        raise EvalError(f"cannot read {ANSWERS}: {error.strerror}") from None
    except (json.JSONDecodeError, ValidationError):
        raise EvalError(f"{ANSWERS} does not hold a valid answers file") from None
    return {str(key): dict(answer) for key, answer in data["answers"].items()}


def known_answer(root: Path, question_id: str) -> Mapping[str, Any]:
    """One known answer; read by code, never printed."""
    answer = known_answers(root).get(question_id)
    if answer is None:
        raise EvalError(f"{ANSWERS} holds no answer for {question_id}")
    return answer


def zero_guarded(known: Mapping[str, Any]) -> bool:
    """A `number` answer equal to 0 (AC-18): a query that finds nothing would score correct."""
    return bool(known["shape"] == "number" and known["value"] == {"int": 0})


@dataclass(frozen=True)
class Layout:
    """The files of one pair, from the known answer's step kinds and the reading note.

    `path_kinds` holds the distinct kind sets of a `path` answer's per path steps,
    sorted, and is empty for every other shape (AC-13 as amended 2026-11-12).
    """

    question_id: str
    store: Store
    kinds: tuple[tuple[str, ...], ...]
    stages: tuple[int, ...]
    path_kinds: tuple[tuple[str, ...], ...] = ()

    def _file(self, part: str) -> Path:
        folder = FOLDERS[self.store]
        return GOLD / folder / f"{self.question_id}.{part}.{folder}"

    @property
    def answer_file(self) -> Path:
        """`gold/<sql|cypher>/Qnn.answer.<ext>`."""
        return self._file("answer")

    @property
    def source_files(self) -> tuple[Path, ...]:
        """One `Qnn.sources.<k>.<ext>` per step of the known answer's source list."""
        return tuple(self._file(f"sources.{k}") for k in range(1, len(self.kinds) + 1))

    @property
    def stage_files(self) -> tuple[Path, ...]:
        """One `Qnn.stage.<n>.<ext>` per numbered source step, only when zero guarded."""
        return tuple(self._file(f"stage.{n}") for n in self.stages)

    @property
    def query_files(self) -> tuple[Path, ...]:
        """Every query file of the pair, in the order a try runs them."""
        return (self.answer_file, *self.source_files, *self.stage_files)

    @property
    def explain_file(self) -> Path:
        """`gold/<sql|cypher>/Qnn.explain.md`."""
        return GOLD / FOLDERS[self.store] / f"{self.question_id}.explain.md"


def layout(question: Question, known: Mapping[str, Any], store: Store) -> Layout:
    """The pair's files: the known step kinds (never IDs) and, when zero guarded, the stages.

    For a `path` answer, also the distinct kind sets of the per path steps (kind names only).
    """
    path_kinds: tuple[tuple[str, ...], ...] = ()
    if known["shape"] == "path":
        path_kinds = tuple(
            sorted({tuple(sorted(step)) for item in known["value"] for step in item["sources"]})
        )
    return Layout(
        question_id=question.id,
        store=store,
        kinds=tuple(tuple(sorted(step)) for step in known["sources"]),
        stages=reading_steps(question.reading) if zero_guarded(known) else (),
        path_kinds=path_kinds,
    )


def layout_message(pair: Layout, missing: tuple[Path, ...]) -> str:
    """Why a pair is refused for a missing file, and the file layout; kind names only.

    A path pair names no step count, hop count or path count, only the column
    sentence and the distinct kind sets of the known per path steps.
    """
    lines = [
        f"{pair.question_id} {pair.store}: missing {', '.join(str(path) for path in missing)}.",
    ]
    if pair.path_kinds:
        sets = "; ".join(", ".join(kinds) or "no kinds" for kinds in pair.path_kinds)
        lines.extend(
            (
                "A path answer: the answer file returns one row per path, a `path` column with"
                " the path's IDs in order and one list column per kind, named after the kind,"
                " with one item per hop.",
                f"Kinds per hop in the known answer: {sets}.",
            )
        )
        lines.append(f"Expected files: {', '.join(str(path) for path in pair.query_files)}")
        return "\n".join(lines)
    steps = "; ".join(
        f"step {k}: {', '.join(kinds) or 'no kinds'}" for k, kinds in enumerate(pair.kinds, 1)
    )
    lines.extend(
        (
            f"The known answer has {len(pair.kinds)} source"
            f" step{'' if len(pair.kinds) == 1 else 's'}" + (f" ({steps})." if pair.kinds else "."),
            "Each sources file returns one column per kind of its step, named after the kind,"
            " with one ID per row.",
        )
    )
    if pair.stages:
        lines.append(
            f"Zero guarded: one stage file per numbered source step"
            f" ({', '.join(str(n) for n in pair.stages)}), each returning one integer."
        )
    lines.append(f"Expected files: {', '.join(str(path) for path in pair.query_files)}")
    return "\n".join(lines)


def read_files(root: Path, paths: tuple[Path, ...]) -> tuple[tuple[str, str, str], ...]:
    """Each file's `(path, text, sha256)`, the hash taken from the very bytes the text came from."""
    read = []
    for path in paths:
        data = (root / path).read_bytes()
        read.append((str(path), data.decode("utf-8"), hashlib.sha256(data).hexdigest()))
    return tuple(read)


@dataclass(frozen=True)
class FileRun:
    """One query file run through its store's executor."""

    path: str
    outcome: FileOutcome
    seconds: float
    result: QueryResult | None
    message: str | None


def _run_each(
    files: tuple[tuple[str, str], ...], execute: Callable[[str], QueryResult]
) -> tuple[FileRun, ...]:
    # Stops at the first file that does not run: the pair is not correct.
    runs: list[FileRun] = []
    for path, text in files:
        started = time.monotonic()
        outcome: FileOutcome = "ok"
        result = None
        message = None
        try:
            result = execute(text)
        except (SqlQueryTimeout, GraphQueryTimeout) as error:
            outcome, message = "store_timeout", str(error)
        except (SqlRowCapError, GraphRowCapError) as error:
            outcome, message = "row_cap", str(error)
        except (SqlQueryError, GraphQueryError) as error:
            outcome, message = "error", str(error)
        runs.append(FileRun(path, outcome, time.monotonic() - started, result, message))
        if outcome != "ok":
            break
    return tuple(runs)


def run_files(
    settings: Settings, store: Store, files: tuple[tuple[str, str], ...]
) -> tuple[FileRun, ...]:
    """Run each `(path, text)` through the store's executor; a lost store stops the command."""
    timeout = settings.query_timeout_s
    try:
        if store == "sql":
            with reader_connection(settings) as conn:
                return _run_each(files, lambda text: run_sql(conn, text, timeout_s=timeout))
        with reader_driver(settings) as driver:
            return _run_each(files, lambda text: run_cypher(driver, text, timeout_s=timeout))
    except (SqlConnectionLost, GraphConnectionLost) as error:
        raise StoreOutage(str(error)) from None


@dataclass(frozen=True)
class Judged:
    """A pair's score, the store's stage counts when zero guarded, and a problem line."""

    scored: Scored
    stage_counts: tuple[int, ...] | None
    problem: str | None


def _stage_count(result: QueryResult) -> int | None:
    if len(result.columns) != 1 or len(result.rows) != 1:
        return None
    value = result.rows[0][result.columns[0]]
    return value if type(value) is int else None


def judge(
    pair: Layout, known: Mapping[str, Any], adapter: Adapter, runs: tuple[FileRun, ...]
) -> Judged:
    """Score a pair from its file runs: a file that did not run fails the pair with its label."""
    for run in runs:
        if run.outcome != "ok":
            line = (run.message or "").splitlines()[0] if run.message else ""
            return Judged(failed(run.outcome), None, f"{run.path}: {mask(line, known)}")
    results = tuple(run.result for run in runs if run.result is not None)
    answer, sources = results[0], results[1 : 1 + len(pair.kinds)]
    stages = results[1 + len(pair.kinds) :]
    stage_counts = None
    if pair.stages:
        counts = tuple(_stage_count(result) for result in stages)
        bad = [
            str(path) for path, count in zip(pair.stage_files, counts, strict=True) if count is None
        ]
        if bad:
            return Judged(
                failed("malformed"), None, f"{', '.join(bad)}: does not return one integer"
            )
        stage_counts = tuple(count for count in counts if count is not None)
    try:
        value, steps = adapter(answer, sources)
    except EvalError as error:
        return Judged(failed("malformed"), stage_counts, f"{pair.question_id}: {error}")
    scored = score(known, str(known["shape"]), value, steps)
    problem = f"{pair.question_id}: {scored.problem}" if scored.problem else None
    return Judged(scored, stage_counts, problem)


def _refuse_state(pair: Layout, state: PairState) -> None:
    if state.discrepancy is not None:
        raise EvalError(
            f"{pair.question_id} {pair.store} is recorded as a known answer discrepancy;"
            " it takes no more tries"
        )
    if state.blocked:
        raise EvalError(
            f"{pair.question_id} {pair.store}: {MAX_FAILED} failed tries in round {state.round};"
            f" run `orgos eval gold decide {pair.question_id} --store {pair.store}`"
            " with --rewrite or --discrepancy"
        )


def try_lines(pair: Layout, state: PairState, outcome: str, judged: Judged | None) -> list[str]:
    """What `gold try` prints: the outcome, the three booleans and the per step counts only."""
    scored = judged.scored if judged is not None else failed("error")
    lines = [
        f"{pair.question_id} {pair.store}: round {state.round}, try {state.next_try},"
        f" outcome {outcome}",
        f"  answer_match: {str(scored.answer_match).lower()}",
        f"  source_match: {str(scored.source_match).lower()}",
        f"  correct: {str(scored.correct).lower()}",
    ]
    lines.extend(
        f"  step {k}: {count.missing} missing, {count.extra} extra"
        for k, count in enumerate(scored.steps, 1)
    )
    if judged is not None and judged.stage_counts is not None:
        shown = ", ".join(
            f"step {n}: {count}" for n, count in zip(pair.stages, judged.stage_counts, strict=True)
        )
        lines.append(f"  stage counts (the store's own): {shown}")
    if judged is not None and judged.problem:
        lines.append(f"  problem: {judged.problem}")
    return lines


def gold_try(
    settings: Settings,
    root: Path,
    question_id: str,
    store: Store,
    *,
    hypothesis: str | None,
    cites: str | None,
) -> tuple[str, ...]:
    """`orgos eval gold try`: refusals first (not tries), then one logged try of the whole pair."""
    require_frozen_set(root)
    question = question_by_id(root, question_id)
    known = known_answer(root, question_id)
    pair = layout(question, known, store)
    state = pair_state(read_tries(root), question_id, store)
    _refuse_state(pair, state)
    missing = tuple(path for path in pair.query_files if not (root / path).is_file())
    if missing:
        raise EvalError(layout_message(pair, missing))
    adapter = ADAPTERS.get(question_id)
    if adapter is None:
        raise EvalError(f"{question_id} has no adapter in orgos/eval/gold_adapters.py")
    if state.next_try >= 2:
        steps = reading_steps(question.reading)
        problem = citation_problem(hypothesis, cites, steps, schema_names(root, load_mapping()))
        if problem is not None:
            raise EvalError(f"{question_id} {store}: {problem}")

    checked = preflight(settings, root)
    if not checked.fingerprint_saved:
        write_fingerprint(root, checked.fingerprint)

    read = read_files(root, pair.query_files)
    files = tuple((path, text) for path, text, _ in read)
    line: dict[str, Any] = {
        "time": now(),
        "event": "try",
        "question": question_id,
        "store": store,
        "round": state.round,
        "try": state.next_try,
        "files": {path: sha for path, _, sha in read},
        "hypothesis": hypothesis if state.next_try >= 2 else None,
        "cites": cites if state.next_try >= 2 else None,
        "load_id": checked.load_id,
        "compare_sha256": sha256_file(root / COMPARE_CODE),
        "git": git_record(root),
    }
    literal = literal_problems(files, known, f"{question.text}\n{question.reading}")
    if literal:
        append(
            root,
            line
            | {
                "answer_match": False,
                "source_match": False,
                "correct": False,
                "outcome": "literal",
                "steps": [],
                "seconds": {},
            },
        )
        raise EvalError(
            "\n".join(
                (
                    *try_lines(pair, state, "literal", None)[:1],
                    "The literal check refused the pair; this counts as a failed try:",
                    *(f"  {problem}" for problem in literal),
                )
            )
        )

    try:
        runs = run_files(settings, store, files)
    except StoreOutage as error:
        raise EvalError(f"{error}; this was not counted as a try, run it again") from None
    judged = judge(pair, known, adapter, runs)
    scored = judged.scored
    append(
        root,
        line
        | {
            "answer_match": scored.answer_match,
            "source_match": scored.source_match,
            "correct": scored.correct,
            "outcome": scored.outcome,
            "steps": [count.record() for count in scored.steps],
            "seconds": {run.path: round(run.seconds, 3) for run in runs},
            **(
                {"stage_counts": list(judged.stage_counts)}
                if judged.stage_counts is not None
                else {}
            ),
        },
    )
    lines = try_lines(pair, state, scored.outcome, judged)
    if scored.outcome == "row_cap":
        raise EvalError("\n".join((*lines, "A gold query hit the row cap: this is a gold bug.")))
    return (*lines, "", *gold_summary(root))


def gold_decide(
    root: Path, question_id: str, store: Store, *, rewrite: bool, discrepancy: str | None
) -> str:
    """`orgos eval gold decide`: after 3 failed tries, a new round or a recorded discrepancy."""
    if rewrite == (discrepancy is not None):
        raise EvalError("give exactly one of --rewrite or --discrepancy TEXT")
    if discrepancy is not None and not discrepancy.strip():
        raise EvalError("--discrepancy needs a text saying what differs")
    question_by_id(root, question_id)
    state = pair_state(read_tries(root), question_id, store)
    if state.discrepancy is not None:
        raise EvalError(f"{question_id} {store} is already recorded as a discrepancy")
    if not state.blocked:
        raise EvalError(
            f"{question_id} {store} is not blocked: {state.failed} failed tries in round"
            f" {state.round}, {MAX_FAILED} needed"
        )
    append(
        root,
        {
            "time": now(),
            "event": "decide",
            "question": question_id,
            "store": store,
            "round": state.round,
            "decision": "rewrite" if rewrite else "discrepancy",
            "text": discrepancy,
        },
    )
    if rewrite:
        return (
            f"{question_id} {store}: round {state.round + 1} opens at try 1;"
            " write the pair again from the reading note"
        )
    return (
        f"{question_id} {store}: recorded as a known answer discrepancy, left out of gold accepted"
    )


# The zero guard, the explain file and acceptance (AC-18 to AC-20, AC-37)


def zero_guard_problems(
    stages: tuple[int, ...], sql: tuple[int, ...] | None, graph: tuple[int, ...] | None
) -> tuple[str, ...]:
    """AC-18 on the stage counts of each store's latest correct try (None: no correct try yet).

    Both stores need a correct try, their counts must be equal step by step, and
    the first count must be above 0, so the population a zero is counted over exists.
    """
    if sql is None or graph is None:
        missing = [store for store, counts in (("sql", sql), ("graph", graph)) if counts is None]
        return (
            f"zero guarded: no correct try yet in {' and '.join(missing)};"
            " a zero guarded pair is accepted only once both stores have one",
        )
    problems = [
        f"zero guarded: stage counts differ at step {step}: sql {left}, graph {right}"
        for step, left, right in zip(stages, sql, graph, strict=True)
        if left != right
    ]
    if sql and sql[0] <= 0:
        problems.append(f"zero guarded: the step {stages[0]} count is {sql[0]}, not above 0")
    return tuple(problems)


def explain_problems(text: str, steps: tuple[int, ...]) -> tuple[str, ...]:
    """Each numbered source step the explain file never names as `step <n>` (AC-19)."""
    named = {int(number) for number in re.findall(r"\bstep (\d+)\b", text)}
    return tuple(f"the explain file never names step {step}" for step in steps if step not in named)


def parse_pins(text: str) -> tuple[tuple[str, str], ...]:
    """The `(sha256, path)` lines of `gold/ACCEPTED`, in `sha256sum` format."""
    pins = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line)
        if match is None:
            raise EvalError(f"{ACCEPTED} line {number}: not `<sha256>  <path>`")
        pins.append((match.group(1), match.group(2)))
    return tuple(pins)


def pinned(current: tuple[tuple[str, str], ...], pair: Layout, files: Mapping[str, str]) -> str:
    """`gold/ACCEPTED` with this pair's lines replaced by `files`, every line sorted by path."""
    prefix = f"{GOLD / FOLDERS[pair.store] / pair.question_id}."
    kept = {path: sha for sha, path in current if not path.startswith(prefix)}
    lines = {**kept, **files}
    return "".join(f"{lines[path]}  {path}\n" for path in sorted(lines))


def accept_problems(
    pair: Layout,
    state: PairState,
    hashes: Mapping[str, str],
    literal: tuple[str, ...],
    explain: tuple[str, ...],
    zero: tuple[str, ...],
    load: tuple[str, ...],
    reviewed: bool,
) -> tuple[str, ...]:
    """Every AC-19 and AC-37 condition the pair does not meet; empty means it is accepted."""
    problems: list[str] = []
    if state.discrepancy is not None:
        problems.append("the pair is recorded as a known answer discrepancy")
    latest = state.latest
    if latest is None:
        problems.append(f"no try yet in round {state.round}")
    elif not latest["correct"]:
        problems.append(
            f"the latest try (round {state.round}, try {latest['try']}) was not correct"
        )
    elif dict(latest["files"]) != {str(path): hashes[str(path)] for path in pair.query_files}:
        problems.append(
            f"a query file changed since round {state.round}, try {latest['try']}; try it again"
        )
    problems.extend(literal)
    problems.extend(explain)
    problems.extend(zero)
    problems.extend(load)
    if not reviewed:
        problems.append("pass --reviewed once you have read every query against gold/CHECKLIST.md")
    return tuple(problems)


def _stage_counts(root: Path, question_id: str, store: Store) -> tuple[int, ...] | None:
    correct = latest_correct(pair_state(read_tries(root), question_id, store))
    if correct is None:
        return None
    return tuple(int(count) for count in correct.get("stage_counts", ()))


def gold_accept(root: Path, question_id: str, store: Store, *, reviewed: bool) -> tuple[str, ...]:
    """`orgos eval gold accept`: pin the pair in `gold/ACCEPTED` when every condition holds.

    Reads no store: L comes from the run records. Logs the round and try the pair passed on.
    """
    require_frozen_set(root)
    question = question_by_id(root, question_id)
    known = known_answer(root, question_id)
    pair = layout(question, known, store)
    lines = read_tries(root)
    state = pair_state(lines, question_id, store)
    missing = tuple(
        path for path in (*pair.query_files, pair.explain_file) if not (root / path).is_file()
    )
    if missing:
        raise EvalError(layout_message(pair, missing))
    # The explain file is read last; each hash comes from the same read as its text.
    read = read_files(root, (*pair.query_files, pair.explain_file))
    files = tuple((path, text) for path, text, _ in read[:-1])
    explain = read[-1][1]
    hashes = {path: sha for path, _, sha in read}
    steps = reading_steps(question.reading)
    zero = (
        zero_guard_problems(
            pair.stages,
            _stage_counts(root, question_id, "sql"),
            _stage_counts(root, question_id, "graph"),
        )
        if pair.stages
        else ()
    )
    problems = accept_problems(
        pair,
        state,
        hashes,
        literal_problems(files, known, f"{question.text}\n{question.reading}"),
        explain_problems(explain, steps),
        zero,
        load_id_problems(lines, question_id, store, record_load_id(root)),
        reviewed,
    )
    latest = state.latest
    if problems or latest is None:
        raise EvalError(f"{question_id} {store} is not accepted:\n  " + "\n  ".join(problems))
    path = root / ACCEPTED
    current = parse_pins(path.read_text(encoding="utf-8")) if path.is_file() else ()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pinned(current, pair, hashes), encoding="utf-8")
    append(
        root,
        {
            "time": now(),
            "event": "accept",
            "question": question_id,
            "store": store,
            "round": state.round,
            "try": latest["try"],
            "files": hashes,
            "load_id": latest["load_id"],
        },
    )
    return (
        f"{question_id} {store}: accepted on round {state.round}, try {latest['try']};"
        f" {len(hashes)} files pinned in {ACCEPTED}",
    )


def pin_problems(root: Path) -> tuple[str, ...]:
    """Each file in `gold/ACCEPTED` that is missing or differs from its hash (AC-20).

    Passes when `gold/ACCEPTED` does not exist. Pre-commit and CI run it on the repo.
    """
    path = root / ACCEPTED
    if not path.is_file():
        return ()
    problems = []
    for expected, pinned_path in parse_pins(path.read_text(encoding="utf-8")):
        if not (root / pinned_path).is_file():
            problems.append(f"{pinned_path} is pinned in {ACCEPTED} but missing")
        elif sha256_file(root / pinned_path) != expected:
            problems.append(
                f"{pinned_path} differs from its hash in {ACCEPTED};"
                " try the pair and accept it again"
            )
    return tuple(problems)


# The gold summary (AC-13, AC-23)


def zero_guarded_ids(known: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """The questions whose known answer is a zero number (today Q03 and Q16)."""
    return frozenset(qid for qid, answer in known.items() if zero_guarded(answer))


def gold_outcome(state: PairState, accepted: bool) -> SummaryOutcome:
    """A pair's latest state: a discrepancy, the latest try's outcome, or `not_tried`.

    A correct latest try counts as `correct` only for a pair `pair_accepted`
    holds for; otherwise it is `correct_not_reviewed`, shown beside the
    headline and never inside it. A literal refusal counts as `error`: the query
    broke a rule, so it is not correct.
    """
    if state.discrepancy is not None:
        return "discrepancy"
    latest = state.latest
    if latest is None:
        return "not_tried"
    outcome = str(latest["outcome"])
    if outcome == "correct":
        return "correct" if accepted else NOT_REVIEWED
    return "error" if outcome == "literal" else cast(ScoredOutcome, outcome)


def pair_accepted(
    pair: Layout,
    lines: tuple[Mapping[str, Any], ...],
    pins: tuple[tuple[str, str], ...],
    holding: frozenset[str],
) -> bool:
    """True when the pins and the log agree the pair passed `gold accept` on its pinned files.

    The pair's pins must cover its full file set (answer, every sources file,
    every stage file, explain) and all hold, and the log must hold an accept
    event for the pair that pinned exactly those hashes and whose round and try
    point to a correct try on the same query file hashes. A hand edited
    `gold/ACCEPTED` therefore counts nothing.
    """
    prefix = f"{GOLD / FOLDERS[pair.store] / pair.question_id}."
    mine = {path: sha for sha, path in pins if path.startswith(prefix)}
    full = (*pair.query_files, pair.explain_file)
    if any(str(path) not in mine for path in full) or any(path not in holding for path in mine):
        return False
    queries = {str(path): mine[str(path)] for path in pair.query_files}

    def of_pair(line: Mapping[str, Any], event: str) -> bool:
        return (
            line.get("event") == event
            and line.get("question") == pair.question_id
            and line.get("store") == pair.store
        )

    tries = {(line["round"], line["try"]): line for line in lines if of_pair(line, "try")}
    for line in lines:
        if not of_pair(line, "accept") or dict(line.get("files", {})) != mine:
            continue
        accepted = tries.get((line.get("round"), line.get("try")))
        if (
            accepted is not None
            and accepted["correct"]
            and dict(accepted.get("files", {})) == queries
        ):
            return True
    return False


def accepted_pairs(
    root: Path, lines: tuple[Mapping[str, Any], ...], pairs: tuple[Layout, ...]
) -> frozenset[tuple[str, str]]:
    """Each `(question, store)` of `pairs` that `pair_accepted` holds for."""
    path = root / ACCEPTED
    if not path.is_file():
        return frozenset()
    pins = parse_pins(path.read_text(encoding="utf-8"))
    holding = frozenset(p for s, p in pins if (root / p).is_file() and sha256_file(root / p) == s)
    return frozenset(
        (pair.question_id, pair.store)
        for pair in pairs
        if pair_accepted(pair, lines, pins, holding)
    )


def gold_entries(
    lines: tuple[Mapping[str, Any], ...],
    questions: tuple[Question, ...],
    accepted: frozenset[tuple[str, str]],
) -> tuple[Entry, ...]:
    """One entry per question and store, from the latest state of every pair in the log."""
    entries = []
    for question in questions:
        for store in STORES:
            tries = [
                line
                for line in lines
                if line.get("event") == "try"
                and line.get("question") == question.id
                and line.get("store") == store
            ]
            entries.append(
                Entry(
                    layer="gold",
                    version="gold",
                    store=store,
                    question_id=question.id,
                    question_type=question.type,
                    data=question.data,
                    outcome=gold_outcome(
                        pair_state(lines, question.id, store), (question.id, store) in accepted
                    ),
                    timeouts=sum(1 for line in tries if line["outcome"] == "store_timeout"),
                    row_caps=sum(1 for line in tries if line["outcome"] == "row_cap"),
                )
            )
    return tuple(entries)


def gold_summary(root: Path) -> list[str]:
    """The gold layer of the summary, computed from the tries log and not stored (AC-13)."""
    lines = read_tries(root)
    questions = read_questions(root)
    known = known_answers(root)
    pairs = tuple(
        layout(question, known[question.id], store)
        for question in questions
        if question.id in known
        for store in STORES
    )
    entries = gold_entries(lines, questions, accepted_pairs(root, lines, pairs))
    return summary_lines(entries, zero_guarded_ids(known))
