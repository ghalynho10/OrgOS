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

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.answers import read_questions
from orgos.eval.frozen import ANSWERS, sha256_file
from orgos.eval.gold_adapters import ADAPTERS, Adapter
from orgos.eval.handoff import STORES, Store
from orgos.eval.literal import literal_problems, mask
from orgos.eval.models import AnswerFile, Question
from orgos.eval.preflight import preflight, record_load_id, write_fingerprint
from orgos.eval.score import Scored, failed, score
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


def known_answer(root: Path, question_id: str) -> Mapping[str, Any]:
    """One known answer in the JSON form `compare` takes; read by code, never printed."""
    try:
        data = json.loads((root / ANSWERS).read_text(encoding="utf-8"))
        AnswerFile.model_validate(data)
    except OSError as error:
        raise EvalError(f"cannot read {ANSWERS}: {error.strerror}") from None
    except (json.JSONDecodeError, ValidationError):
        raise EvalError(f"{ANSWERS} does not hold a valid answers file") from None
    answer = data["answers"].get(question_id)
    if answer is None:
        raise EvalError(f"{ANSWERS} holds no answer for {question_id}")
    return dict(answer)


def zero_guarded(known: Mapping[str, Any]) -> bool:
    """A `number` answer equal to 0 (AC-18): a query that finds nothing would score correct."""
    return bool(known["shape"] == "number" and known["value"] == {"int": 0})


@dataclass(frozen=True)
class Layout:
    """The files of one pair, from the known answer's step kinds and the reading note."""

    question_id: str
    store: Store
    kinds: tuple[tuple[str, ...], ...]
    stages: tuple[int, ...]

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
    """The pair's files: the known step kinds (never IDs) and, when zero guarded, the stages."""
    return Layout(
        question_id=question.id,
        store=store,
        kinds=tuple(tuple(sorted(step)) for step in known["sources"]),
        stages=reading_steps(question.reading) if zero_guarded(known) else (),
    )


def layout_message(pair: Layout, missing: tuple[Path, ...]) -> str:
    """Why a pair is refused for a missing file, and the file layout; kind names only."""
    steps = "; ".join(
        f"step {k}: {', '.join(kinds) or 'no kinds'}" for k, kinds in enumerate(pair.kinds, 1)
    )
    lines = [
        f"{pair.question_id} {pair.store}: missing {', '.join(str(path) for path in missing)}.",
        f"The known answer has {len(pair.kinds)} source step{'' if len(pair.kinds) == 1 else 's'}"
        + (f" ({steps})." if pair.kinds else "."),
        "Each sources file returns one column per kind of its step, named after the kind,"
        " with one ID per row.",
    ]
    if pair.stages:
        lines.append(
            f"Zero guarded: one stage file per numbered source step"
            f" ({', '.join(str(n) for n in pair.stages)}), each returning one integer."
        )
    lines.append(f"Expected files: {', '.join(str(path) for path in pair.query_files)}")
    return "\n".join(lines)


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
        raise EvalError(f"{error}; this was not counted as a try, run it again") from None


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

    files = tuple(
        (str(path), (root / path).read_text(encoding="utf-8")) for path in pair.query_files
    )
    line: dict[str, Any] = {
        "time": now(),
        "event": "try",
        "question": question_id,
        "store": store,
        "round": state.round,
        "try": state.next_try,
        "files": {path: sha256_file(root / path) for path, _ in files},
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

    runs = run_files(settings, store, files)
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
    return tuple(lines)


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
    files = tuple(
        (str(path), (root / path).read_text(encoding="utf-8")) for path in pair.query_files
    )
    pinned_files = (*pair.query_files, pair.explain_file)
    hashes = {str(path): sha256_file(root / path) for path in pinned_files}
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
        explain_problems((root / pair.explain_file).read_text(encoding="utf-8"), steps),
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
