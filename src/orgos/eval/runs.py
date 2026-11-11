"""Agent runs: loading the agent, the plan and cost gate, the versions log and run files.

Spec 0011 AC-26 to AC-38. The runner never imports the agent: `--agent` is
`replay` or a dotted `module:factory` loaded at run time and called as
`factory(settings)`. A real agent runs only under a written `PLAN.json`, `--go`
and `--max-cost-usd`, and only for a version logged in `versions.jsonl` (at
most 3 entries, append only). Each run is scored by `store_answer` and
`compare` and written once to its own file through a temporary file and an
exclusive link, so two runs cannot overwrite each other and a resume fills only
the missing run numbers. Replay is exempt from the plan, the gate and the
versions log, and is never counted as an agent result.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.answers import read_questions
from orgos.eval.compare import store_answer
from orgos.eval.frozen import sha256_file
from orgos.eval.gold import (
    COMPARE_CODE,
    known_answers,
    parse_store,
    question_by_id,
    zero_guarded,
    zero_guarded_ids,
)
from orgos.eval.handoff import (
    STORES,
    AgentDescription,
    AgentRun,
    ProviderOutage,
    QueryAgent,
    QueryAttempt,
    Store,
    StoreOutage,
    Usage,
)
from orgos.eval.literal import mask
from orgos.eval.models import QUESTION_COUNT, Question
from orgos.eval.preflight import preflight
from orgos.eval.replay import REPLAY, ReplayAgent
from orgos.eval.score import Entry, Layer, Scored, failed, groups, score, summary_lines
from orgos.eval.tries import git_record, now

log = logging.getLogger(__name__)

RUNS_DIR = Path("results/agent_runs")
VERSIONS = RUNS_DIR / "versions.jsonl"
PLAN = "PLAN.json"
SUMMARY = "summary.json"
MAX_VERSIONS = 3
KINDS = ("bug_fix", "behaviour_change")
# A store answer bigger than this is kept as its hash and counts (AC-31).
STORE_ANSWER_MAX_BYTES = 20 * 1024
_RUN_FILE = re.compile(r"(Q\d{2})-(sql|graph)-(\d+)\.json")


# Loading the agent (AC-38)


def load_agent(spec: str, settings: Settings, root: Path) -> QueryAgent:
    """`replay`, or a `module:factory` path whose factory returns a `QueryAgent`."""
    if spec == REPLAY:
        return ReplayAgent(settings, root)
    module_name, sep, attribute = spec.partition(":")
    if not sep or not module_name or not attribute:
        raise EvalError(f"--agent must be replay or module:factory, not {spec!r}")
    try:
        factory: Callable[[Settings], Any] = getattr(
            importlib.import_module(module_name), attribute
        )
    except (ImportError, AttributeError) as error:
        raise EvalError(f"cannot load the agent {spec}: {error}") from None
    agent = factory(settings)
    if not (callable(getattr(agent, "describe", None)) and callable(getattr(agent, "run", None))):
        raise EvalError(f"{spec} did not return a QueryAgent (no describe and run)")
    return agent  # type: ignore[no-any-return]


# Files (AC-29)


def version_dir(root: Path, version: str) -> Path:
    """`results/agent_runs/<version>/`."""
    return root / RUNS_DIR / version


def run_path(root: Path, version: str, question_id: str, store: Store, number: int) -> Path:
    """`results/agent_runs/<version>/Qnn-<store>-<n>.json`."""
    return version_dir(root, version) / f"{question_id}-{store}-{number}.json"


def write_once(path: Path, data: Mapping[str, Any]) -> None:
    """Write a file once: a temporary file, then an exclusive link to the final name.

    A killed write leaves at most a hidden temporary file, never a half run file,
    and an existing file is never opened for writing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError:
        raise EvalError(f"{path} already exists; a run file is written once") from None
    finally:
        os.unlink(temporary)


def read_run_files(root: Path, version: str) -> tuple[Mapping[str, Any], ...]:
    """Every finished run file of a version, in name order."""
    folder = version_dir(root, version)
    if not folder.is_dir():
        return ()
    return tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(folder.iterdir())
        if _RUN_FILE.fullmatch(path.name)
    )


# The plan and the cost gate (AC-30)


@dataclass(frozen=True)
class Plan:
    """What `PLAN.json` holds for one agent version."""

    runs: int
    request_limit: int
    token_limit: int
    tool_call_limit: int
    query_timeout_s: int
    model_id: str
    price_table: str
    usd_per_million_input: float
    usd_per_million_output: float
    estimate_usd: float


def upper_bound(runs: int, token_limit: int, prices: AgentDescription | Plan) -> float:
    """Runs times the token limit times the higher price per million tokens: an upper bound."""
    price = max(prices.usd_per_million_input, prices.usd_per_million_output)
    return runs * token_limit * price / 1_000_000


def make_plan(settings: Settings, description: AgentDescription) -> Plan:
    """The plan for a version: all 20 questions, the runs per question, both stores."""
    token_limit = settings.agent_token_limit
    planned = QUESTION_COUNT * settings.eval_runs * len(STORES)
    return Plan(
        runs=settings.eval_runs,
        request_limit=settings.agent_request_limit,
        token_limit=token_limit,
        tool_call_limit=settings.agent_tool_call_limit,
        query_timeout_s=settings.query_timeout_s,
        model_id=description.model_id,
        price_table=description.price_table,
        usd_per_million_input=description.usd_per_million_input,
        usd_per_million_output=description.usd_per_million_output,
        estimate_usd=upper_bound(planned, token_limit, description),
    )


def write_plan(root: Path, version: str, plan: Plan) -> Path:
    """Write `PLAN.json` once for a version; a written plan is not rewritten."""
    path = version_dir(root, version) / PLAN
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(asdict(plan), handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError:
        shown = path.relative_to(root)
        raise EvalError(f"{shown} exists; a written plan is not rewritten") from None
    return path


def read_plan(root: Path, version: str) -> Plan | None:
    """The version's plan, or None before `run --estimate` wrote it."""
    path = version_dir(root, version) / PLAN
    if not path.is_file():
        return None
    return Plan(**json.loads(path.read_text(encoding="utf-8")))


def gate_problem(
    *,
    go: bool,
    cap: float | None,
    finished_costs: Sequence[float],
    finished_requests: Sequence[int],
    to_start: int,
) -> str | None:
    """Why a real agent run is refused before it starts, or None (AC-30).

    A finished run that reports 0 requests, or a measured mean of 0, means the
    agent did not read its usage, so the mean cannot bound the cost.
    """
    if not go:
        return "a real agent runs only with --go"
    if cap is None:
        return "a real agent runs only with --max-cost-usd"
    if not finished_costs:
        if to_start > 1:
            return (
                "this version has no finished run file yet: run one run of one question in one"
                " store first (--only Qnn --store S --max-run 1)"
            )
        return None
    unread = (
        "the agent did not read its usage on every exit (spec 0012 AC-23), so the measured"
        " mean cannot bound the cost"
    )
    zero = sum(1 for requests in finished_requests if requests == 0)
    if zero:
        files = "1 finished run file reports" if zero == 1 else f"{zero} finished run files report"
        return f"{files} 0 requests: {unread}"
    mean = sum(finished_costs) / len(finished_costs)
    if mean <= 0:
        return f"the measured mean cost is ${mean:.4f}: {unread}"
    if mean * to_start > cap:
        return (
            f"the measured mean cost ${mean:.4f} times {to_start} runs is"
            f" ${mean * to_start:.4f}, above the cap ${cap:.4f}"
        )
    return None


# The versions log (AC-32)


def read_versions(root: Path) -> tuple[Mapping[str, Any], ...]:
    """Every entry of `versions.jsonl`, oldest first."""
    path = root / VERSIONS
    if not path.is_file():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())


def changed_parts(previous: Mapping[str, str], parts: Mapping[str, str]) -> tuple[str, ...]:
    """Every part added, removed or with another hash since the previous entry."""
    names = {*previous, *parts}
    return tuple(sorted(name for name in names if previous.get(name) != parts.get(name)))


def version_entry(
    entries: Sequence[Mapping[str, Any]],
    description: AgentDescription,
    *,
    reason: str | None,
    exposed_by: str | None,
    kind: str | None,
    exposed_exists: bool,
) -> dict[str, Any]:
    """The next versions log entry, or an `EvalError` for a rule it breaks (AC-32)."""
    if len(entries) >= MAX_VERSIONS:
        raise EvalError(f"{VERSIONS} already holds {MAX_VERSIONS} versions; a fourth is refused")
    if any(entry["version_hash"] == description.version_hash for entry in entries):
        raise EvalError(f"version {description.version_hash} is already logged")
    entry: dict[str, Any] = {
        "time": now(),
        "version_hash": description.version_hash,
        "model_id": description.model_id,
        "parts": dict(description.parts),
    }
    if not entries:
        return entry
    changed = changed_parts(entries[-1]["parts"], description.parts)
    if not changed:
        raise EvalError("no part changed since the previous version; nothing to log")
    if not reason or not exposed_by or kind not in KINDS:
        raise EvalError(
            "a version after the first needs --reason, --exposed-by (the run file that"
            " exposed the problem) and --kind bug_fix or behaviour_change"
        )
    if not exposed_exists:
        raise EvalError(f"--exposed-by {exposed_by} is not a run file")
    if kind == "bug_fix" and any("instruction" in name for name in changed):
        raise EvalError("a change to instruction wording is a behaviour change, not a bug fix")
    return entry | {
        "changed": list(changed),
        "reason": reason,
        "exposed_by": exposed_by,
        "kind": kind,
    }


def version_add(
    settings: Settings,
    root: Path,
    agent_spec: str,
    *,
    reason: str | None,
    exposed_by: str | None,
    kind: str | None,
) -> str:
    """`orgos eval version add`: append the agent's version to the versions log."""
    if agent_spec == REPLAY:
        raise EvalError("replay is exempt from the versions log")
    description = load_agent(agent_spec, settings, root).describe()
    entries = read_versions(root)
    entry = version_entry(
        entries,
        description,
        reason=reason,
        exposed_by=exposed_by,
        kind=kind,
        exposed_exists=exposed_by is not None and (root / exposed_by).is_file(),
    )
    path = root / VERSIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    changed = ", ".join(entry.get("changed", ())) or "none (first version)"
    return (
        f"logged version {description.version_hash} ({len(entries) + 1} of {MAX_VERSIONS});"
        f" changed: {changed}"
    )


# Scoring and recording one run (AC-22, AC-26, AC-27, AC-31)


def retries(attempts: Sequence[QueryAttempt]) -> int:
    """The attempts that follow an attempt whose outcome was not `ok` (AC-27)."""
    return sum(1 for previous in attempts[:-1] if previous.outcome != "ok")


def limit_reached(usage: Usage, plan: Plan | None) -> str | None:
    """The limit a `limit_hit` run reached, in the order requests, tool calls, tokens."""
    if plan is None:
        return None
    if usage.requests >= plan.request_limit:
        return "requests"
    if usage.tool_calls >= plan.tool_call_limit:
        return "tool_calls"
    if usage.input_tokens + usage.output_tokens >= plan.token_limit:
        return "tokens"
    return None


def score_run(known: Mapping[str, Any], run: AgentRun) -> Scored:
    """An answered run scored by `store_answer` and `compare`; any other end scored as its label."""
    if run.outcome != "answered":
        return failed(run.outcome)
    if run.shape is None:
        return Scored(False, False, False, "malformed", (), "an answered run has no shape")
    return score(known, run.shape, run.value, run.sources)


def stored_answer(run: AgentRun) -> dict[str, Any] | None:
    """The store answer in full when its JSON is 20 KB or less, else its hash and counts."""
    if run.shape is None:
        return None
    try:
        converted = store_answer(run.shape, run.value, run.sources)
    except EvalError:
        return None
    text = json.dumps(converted, sort_keys=True)
    if len(text.encode()) <= STORE_ANSWER_MAX_BYTES:
        return {"full": converted}
    value = converted["value"]
    return {
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "value_items": len(value) if isinstance(value, list) else 1,
        "source_ids": [sum(len(ids) for ids in step.values()) for step in converted["sources"]],
    }


def run_record(
    *,
    question: Question,
    store: Store,
    number: int,
    version: str,
    plan: Plan | None,
    settings: Settings,
    load_id: str,
    known: Mapping[str, Any],
    run: AgentRun,
    scored: Scored,
    compare_sha256: str,
    git: Mapping[str, Any],
) -> dict[str, Any]:
    """Everything AC-22 and AC-31 ask a run file to hold; no connection detail."""
    return {
        "time": now(),
        "question": question.id,
        "store": store,
        "run": number,
        "agent_version": version,
        "plan": asdict(plan) if plan is not None else {"runs": settings.eval_runs},
        "query_timeout_s": settings.query_timeout_s,
        "load_id": load_id,
        "type": question.type,
        "data": question.data,
        "zero_answer": zero_guarded(known),
        "response_model": run.response_model,
        "run_outcome": run.outcome,
        "answer_match": scored.answer_match,
        "source_match": scored.source_match,
        "correct": scored.correct,
        "outcome": scored.outcome,
        "problem": scored.problem,
        "steps": [count.record() for count in scored.steps],
        "attempts": [asdict(attempt) for attempt in run.attempts],
        "attempt_count": len(run.attempts),
        "retries": retries(run.attempts),
        "limit": limit_reached(run.usage, plan) if run.outcome == "limit_hit" else None,
        "usage": asdict(run.usage),
        "seconds": run.seconds,
        "store_answer": stored_answer(run),
        "compare_sha256": compare_sha256,
        "git": dict(git),
    }


def fallback_record(
    *, item: Work, version: str, load_id: str, run: AgentRun, problem: str
) -> dict[str, Any]:
    """A paid run whose scoring or writing failed: outcome `error`, its usage and the problem.

    Written in place of the run file, so the cost is kept and a resume does not
    run it again unseen. It holds every field the summary reads.
    """
    return {
        "time": now(),
        "question": item.question.id,
        "store": item.store,
        "run": item.number,
        "agent_version": version,
        "load_id": load_id,
        "type": item.question.type,
        "data": item.question.data,
        "fallback": True,
        "response_model": run.response_model,
        "run_outcome": run.outcome,
        "answer_match": False,
        "source_match": False,
        "correct": False,
        "outcome": "error",
        "problem": problem,
        "steps": [],
        "attempts": [],
        "attempt_count": len(run.attempts),
        "retries": retries(run.attempts),
        "limit": None,
        "usage": asdict(run.usage),
        "seconds": run.seconds,
    }


def _first_line(error: BaseException, known: Mapping[str, Any]) -> str:
    detail = str(error).splitlines()[0] if str(error) else ""
    return mask(f"{type(error).__name__}: {detail}" if detail else type(error).__name__, known)


def keep_failed_run(
    root: Path,
    path: Path,
    *,
    item: Work,
    version: str,
    load_id: str,
    run: AgentRun,
    known: Mapping[str, Any],
    error: BaseException,
    spent: float,
) -> str:
    """Write a paid run that failed after the call as a fallback file; return what to print.

    The problem text is masked like a store error, so it never shows a known value.
    """
    problem = _first_line(error, known)
    shown = path.relative_to(root)
    try:
        record = fallback_record(
            item=item, version=version, load_id=load_id, run=run, problem=problem
        )
        write_once(path, record)
        kept = f"wrote {shown} with outcome error and the run's usage, so a resume skips it"
    except (EvalError, OSError, TypeError, ValueError) as failed_write:
        kept = f"could not write {shown} either ({_first_line(failed_write, known)})"
    return (
        f"{item.question.id} {item.store} run {item.number} failed after the model call:"
        f" {problem}. It cost ${run.usage.cost_usd:.4f}; this invocation spent ${spent:.4f}."
        f" {kept}"
    )


# The summary of a version (AC-23, AC-33, AC-34)


def run_entries(
    files: Sequence[Mapping[str, Any]],
    version: str,
    questions: Sequence[Question],
    runs: int | None,
) -> tuple[Entry, ...]:
    """One entry per finished run, and per planned run with no file when `runs` is given."""
    layer: Layer = "replay" if version == REPLAY else "agent"
    found = {(f["question"], f["store"], f["run"]): f for f in files}
    by_id = {question.id: question for question in questions}
    entries = [
        Entry(
            layer=layer,
            version=version,
            store=f["store"],
            question_id=f["question"],
            question_type=f["type"],
            data=f["data"],
            outcome=f["outcome"],
            attempts=f["attempt_count"],
            retries=f["retries"],
            timeouts=sum(1 for a in f["attempts"] if a["outcome"] == "store_timeout"),
            row_caps=sum(1 for a in f["attempts"] if a["outcome"] == "row_cap"),
            limit=f["limit"],
        )
        for f in files
    ]
    if runs is not None and layer == "agent":
        entries.extend(
            Entry(
                layer=layer,
                version=version,
                store=store,
                question_id=question_id,
                question_type=question.type,
                data=question.data,
                outcome="not_tried",
                missing=f"{question_id}-{store}-{number}",
            )
            for question_id, question in sorted(by_id.items())
            for store in STORES
            for number in range(1, runs + 1)
            if (question_id, store, number) not in found
        )
    return tuple(entries)


# The run command (AC-28 to AC-38)


@dataclass(frozen=True)
class Work:
    """One run to start: a question, a store and a run number."""

    question: Question
    store: Store
    number: int


def planned_work(
    root: Path,
    version: str,
    questions: Sequence[Question],
    stores: Sequence[Store],
    max_run: int,
) -> tuple[Work, ...]:
    """Every run number from 1 to `max_run` of each question and store with no file yet."""
    return tuple(
        Work(question, store, number)
        for question in questions
        for store in stores
        for number in range(1, max_run + 1)
        if not run_path(root, version, question.id, store, number).exists()
    )


def load_id_problems(files: Sequence[Mapping[str, Any]], load_id: str) -> tuple[str, ...]:
    """Each load ID other than L that a finished run file of the version holds (AC-37)."""
    other = sorted({str(f["load_id"]) for f in files if f["load_id"] != load_id})
    return tuple(
        f"run files of this version hold load ID {held}, the current load ID is {load_id}"
        for held in other
    )


def _questions(root: Path, only: str | None) -> tuple[Question, ...]:
    if only is None:
        return read_questions(root)
    return (question_by_id(root, only),)


def estimate(
    settings: Settings, root: Path, agent_spec: str, *, only: str | None
) -> tuple[str, ...]:
    """`orgos eval run --estimate`: write `PLAN.json` for the agent's version; no model call."""
    if agent_spec == REPLAY:
        raise EvalError("replay is exempt from the plan; run it without --estimate")
    description = load_agent(agent_spec, settings, root).describe()
    plan = make_plan(settings, description)
    path = write_plan(root, description.version_hash, plan)
    lines = [
        f"wrote {path.relative_to(root)} for version {description.version_hash}",
        f"  {plan.runs} runs per question per store, model {plan.model_id},"
        f" prices {plan.price_table}",
        f"  limits: requests {plan.request_limit}, tokens {plan.token_limit},"
        f" tool calls {plan.tool_call_limit}; query timeout {plan.query_timeout_s} s",
        f"  upper bound for all {QUESTION_COUNT} questions, both stores: ${plan.estimate_usd:.2f}",
    ]
    if only is not None:
        question_by_id(root, only)
        bound = upper_bound(plan.runs * len(STORES), plan.token_limit, description)
        lines.append(f"  upper bound for {only} only: ${bound:.2f}")
    return tuple(lines)


def run_work(
    root: Path,
    settings: Settings,
    agent: QueryAgent,
    work: Sequence[Work],
    *,
    version: str,
    plan: Plan | None,
    load_id: str,
    known: Mapping[str, Mapping[str, Any]],
    cap: float | None,
) -> tuple[int, float, list[str]]:
    """Run each item, score it and write its file once; stop starting runs at the cap.

    A provider or store outage stops the command with how to resume (AC-28);
    anything the agent produced, no answer and limits included, is scored.
    Returns the files written, the cost spent and a line when the cap stopped it.
    """
    compare_sha256 = sha256_file(root / COMPARE_CODE)
    git = git_record(root)
    spent = 0.0
    written = 0
    for item in work:
        if cap is not None and spent >= cap:
            return (
                written,
                spent,
                [f"stopped: the cost so far, ${spent:.4f}, reached the cap ${cap:.4f}"],
            )
        try:
            run = agent.run(item.question, item.store)
        except (ProviderOutage, StoreOutage) as error:
            raise EvalError(
                f"{type(error).__name__}: {error}. Finished run files are kept; run the same"
                " command again to fill only the missing run numbers"
            ) from None
        spent += run.usage.cost_usd
        answer = known[item.question.id]
        path = run_path(root, version, item.question.id, item.store, item.number)
        try:
            scored = score_run(answer, run)
            record = run_record(
                question=item.question,
                store=item.store,
                number=item.number,
                version=version,
                plan=plan,
                settings=settings,
                load_id=load_id,
                known=answer,
                run=run,
                scored=scored,
                compare_sha256=compare_sha256,
                git=git,
            )
            write_once(path, record)
        # The run is paid for: whatever failed, its usage must reach a file before stopping.
        except Exception as error:
            raise EvalError(
                keep_failed_run(
                    root,
                    path,
                    item=item,
                    version=version,
                    load_id=load_id,
                    run=run,
                    known=answer,
                    error=error,
                    spent=spent,
                )
            ) from error
        written += 1
        log.info("%s %s run %s: %s", item.question.id, item.store, item.number, scored.outcome)
    return written, spent, []


def run_agent(
    settings: Settings,
    root: Path,
    agent_spec: str,
    *,
    go: bool,
    max_cost_usd: float | None,
    only: str | None,
    store: str | None,
    max_run: int | None,
) -> tuple[str, ...]:
    """`orgos eval run`: the preflight, the gate, then each missing run, written once."""
    checked = preflight(settings, root)
    agent = load_agent(agent_spec, settings, root)
    description = agent.describe()
    replay = agent_spec == REPLAY
    version = REPLAY if replay else description.version_hash
    plan = None if replay else read_plan(root, version)
    if not replay:
        if not any(e["version_hash"] == version for e in read_versions(root)):
            raise EvalError(
                f"version {version} has no entry in {VERSIONS}; run `orgos eval version add` first"
            )
        if plan is None:
            raise EvalError(
                f"version {version} has no {PLAN}; run `orgos eval run --estimate` first"
            )
        if plan.query_timeout_s != settings.query_timeout_s:
            raise EvalError(
                f"the {PLAN} of version {version} holds a query timeout of"
                f" {plan.query_timeout_s} s, the current setting is {settings.query_timeout_s} s;"
                f" set ORGOS_QUERY_TIMEOUT_S={plan.query_timeout_s} so every run of the version"
                " has the same timeout"
            )
    files = read_run_files(root, version)
    problems = load_id_problems(files, checked.load_id)
    if problems:
        raise EvalError("\n".join(problems))
    questions = _questions(root, only)
    stores = (parse_store(store),) if store is not None else STORES
    runs = plan.runs if plan is not None else settings.eval_runs
    work = planned_work(root, version, questions, stores, max_run or runs)
    lines: list[str] = []
    if plan is not None:
        price = max(plan.usd_per_million_input, plan.usd_per_million_output)
        lines.append(
            f"upper bound for one run: ${upper_bound(1, plan.token_limit, plan):.4f}"
            f" (the token limit {plan.token_limit:,} times the higher price ${price:g} per million)"
        )
        costs = [float(f["usage"]["cost_usd"]) for f in files]
        if costs:
            mean = sum(costs) / len(costs)
            lines.append(f"measured mean cost of {len(costs)} finished runs: ${mean:.4f}")
        problem = gate_problem(
            go=go,
            cap=max_cost_usd,
            finished_costs=costs,
            finished_requests=[int(f["usage"]["requests"]) for f in files],
            to_start=len(work),
        )
        if problem is not None:
            raise EvalError("\n".join((*lines, problem)))
    if not work:
        lines.append("every planned run number already has a file; nothing to run")

    known = known_answers(root)
    written, spent, stopped = run_work(
        root,
        settings,
        agent,
        work,
        version=version,
        plan=plan,
        load_id=checked.load_id,
        known=known,
        cap=None if replay else max_cost_usd,
    )
    lines.extend(stopped)

    files = read_run_files(root, version)
    entries = run_entries(files, version, read_questions(root), None if replay else runs)
    zero = zero_guarded_ids(known)
    summary = [group.record() for group in groups(entries, zero)]
    write_summary(root, version, summary)
    lines.append(f"wrote {written} run files under {version_dir(root, version).relative_to(root)}/")
    if not replay:
        total = sum(float(f["usage"]["cost_usd"]) for f in files)
        lines.append(
            f"cost this invocation ${spent:.4f}; cumulative for version {version} ${total:.4f}"
        )
    return (*lines, "", *summary_lines(entries, zero))


def write_summary(root: Path, version: str, summary: Sequence[Mapping[str, Any]]) -> None:
    """The summary record of a version, rewritten after each `run` (AC-23)."""
    path = version_dir(root, version) / SUMMARY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8")
