"""The `orgos` command line entry point.

`app` holds the commands; `run()` is the script entry. Each command loads
`Settings` itself, so help never needs a `.env`. `run()` is the one place
that prints known errors and picks exit codes, through `EXIT_CODES`; a later
feature adds its command and one line there.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from orgos import __version__
from orgos.agent.ask import render_run, usage_line
from orgos.agent.errors import AgentError
from orgos.config import ConfigError, Settings, load_settings
from orgos.eval import EvalError
from orgos.eval.answers import answers
from orgos.eval.checker import check_files
from orgos.eval.describe import describe
from orgos.eval.gold import gold_accept, gold_decide, gold_try, parse_store
from orgos.eval.handoff import RunStopped
from orgos.eval.preflight import preflight, summary
from orgos.eval.runs import (
    estimate,
    estimate_plain,
    read_run_files,
    run_agent,
    run_plain_agent,
    upper_bound,
    version_add,
    version_dir,
)
from orgos.graphstore import GraphStoreError
from orgos.graphstore.load import load_graph
from orgos.ingest import IngestError
from orgos.mongostore import MongoStoreError
from orgos.mongostore.restore import restore
from orgos.mongostore.verify import verify
from orgos.sqlstore import SqlStoreError
from orgos.sqlstore.bootstrap import bootstrap
from orgos.sqlstore.load import load_sql

if TYPE_CHECKING:
    from orgos.agent.agent import OrgosQueryAgent

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(levelname)s %(message)s"
log = logging.getLogger(__name__)

# Named errors and their exit codes, checked in order. Anything else keeps its
# traceback. Click already uses 2 for a usage error.
EXIT_CODES: tuple[tuple[type[Exception], int], ...] = (
    (ConfigError, 2),
    (SqlStoreError, 1),
    (MongoStoreError, 3),
    (EvalError, 4),
    (IngestError, 5),
    (GraphStoreError, 6),
    (AgentError, 7),
)
NAMED_ERRORS = tuple(error for error, _ in EXIT_CODES)

app = typer.Typer(
    name="orgos",
    help="OrgOS: query one company's work data through a SQL store and a graph store.",
    no_args_is_help=True,
    add_completion=False,
    # `bootstrap()` holds a connection string with the superuser password in a
    # local; a Typer upgrade must not start printing locals in tracebacks.
    pretty_exceptions_show_locals=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"orgos {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_print_version,
        is_eager=True,
    ),
) -> None:
    """OrgOS: query one company's work data through a SQL store and a graph store."""


StoreOption = Annotated[str, typer.Option("--store", help="sql or graph.")]


def _build_agent(settings: Settings) -> OrgosQueryAgent:
    # Imported here, so `--help` and the other commands never load the model library.
    from orgos.agent.agent import build_agent

    return build_agent(settings)


def ask_lines(
    settings: Settings,
    root: Path,
    text: str,
    store: str,
    *,
    go: bool,
    check: Callable[[Settings, Path], object] = preflight,
    build: Callable[[Settings], OrgosQueryAgent] = _build_agent,
) -> tuple[str, ...]:
    """`orgos ask`: preflight, build, print the cost bound, then one plain run if `--go` is given.

    Progress and the cost lines go to the log (stderr); the returned lines are
    the answer, the sources and the queries, for stdout. Writes no file.
    """
    chosen = parse_store(store)
    check(settings, root)
    log.info("ask: building the agent (session check, structure and value reads)")
    agent = build(settings)
    description = agent.describe()
    price = max(description.usd_per_million_input, description.usd_per_million_output)
    log.info(
        "ask: upper bound for one run: $%.4f (the token limit %s times the higher price $%g"
        " per million)",
        upper_bound(1, settings.agent_token_limit, description),
        f"{settings.agent_token_limit:,}",
        price,
    )
    costs = [
        float(f["usage"]["cost_usd"])
        for f in read_run_files(version_dir(root, description.version_hash))
    ]
    if costs:
        log.info(
            "ask: measured mean cost of %d finished runs of this version: $%.4f",
            len(costs),
            sum(costs) / len(costs),
        )
    if not go:
        raise AgentError("orgos ask makes paid model calls; add --go to run it")
    log.info("ask: running on %s", chosen)
    try:
        run = agent.ask(text, chosen)
    # An outage or a refusal stops the command; its cost is still printed (spec 0012 AC-23).
    except RunStopped as error:
        raise error.restated(f"{error}. It cost ${error.cost_usd:.4f}") from None
    log.info("ask: %s", usage_line(run))
    return render_run(run)


@app.command("ask")
def ask_command(
    question: Annotated[str, typer.Argument(help="The question, in plain words.")],
    store: StoreOption,
    go: Annotated[bool, typer.Option("--go", help="Allow the paid model calls.")] = False,
) -> None:
    """Ask the agent one plain question of one store; not scored, writes no file.

    Runs the preflight first, prints the upper bound for one run (and the mean of
    finished runs of this version), and refuses without --go. Prints the answer,
    the sources and each query to stdout; progress goes to stderr.
    """
    settings = load_settings()
    for line in ask_lines(settings, Path.cwd(), question, store, go=go):
        typer.echo(line)


@app.command("bootstrap")
def bootstrap_command() -> None:
    """Create or reset the Postgres database, roles, schemas, and grants (rerunnable)."""
    settings = load_settings()
    bootstrap(settings)


mongo_app = typer.Typer(
    name="mongo",
    help="Restore the pinned Jira dump into the Mongo staging store and verify it.",
    no_args_is_help=True,
)
app.add_typer(mongo_app)


@mongo_app.command("restore")
def mongo_restore_command(
    check: bool = typer.Option(
        False, "--check", help="Run the checks only; write nothing to Mongo."
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="After every check passes, delete the restore record and drop JiraReposAnon first.",
    ),
) -> None:
    """Check, then stream the dump from the zip into Mongo and verify every count.

    Skips when already restored. Run `scripts/fetch_data.sh` first; it writes
    the receipt this reads.
    """
    settings = load_settings()
    restore(settings, reset=reset, check_only=check)


@mongo_app.command("verify")
def mongo_verify_command() -> None:
    """Compare the live JiraReposAnon with its restore record (changes nothing)."""
    settings = load_settings()
    verify(settings)


eval_app = typer.Typer(
    name="eval",
    help="Describe the data, check the hand written questions, and compute their known answers.",
    no_args_is_help=True,
)
app.add_typer(eval_app)

EVAL_DIR = Path("eval")
QUESTIONS_FILE = EVAL_DIR / "questions.toml"


@eval_app.command("describe")
def eval_describe_command() -> None:
    """Describe the corpus for question writing: write eval/data-description.md.

    Runs only the allowed reads through the reader login and appends each
    query's text to eval/exploration.log. Writes nothing to Mongo.
    """
    settings = load_settings()
    typer.echo(describe(settings, EVAL_DIR).summary)


@eval_app.command("check-questions")
def eval_check_questions_command(
    file: Annotated[
        Path, typer.Option("--file", help="The question file to check.")
    ] = QUESTIONS_FILE,
) -> None:
    """Check the questions (and eval/amendments.toml when present) against every checker rule.

    Lists every broken rule together. Reads no database.
    """
    typer.echo(check_files(file, EVAL_DIR / "data-description.md", EVAL_DIR / "amendments.toml"))


@eval_app.command("answers")
def eval_answers_command(
    check: bool = typer.Option(
        False, "--check", help="Recompute in memory; pass only if eval/answers.json is identical."
    ),
    only: Annotated[
        str | None,
        typer.Option(
            "--only", help="Compute and print one answer (for example Q01); write nothing."
        ),
    ] = None,
) -> None:
    """Compute the known answers over raw Mongo through the reader login.

    Runs the guards first and stops at the first that fails. A full run writes
    eval/answers.json and results/known_answers/run_record.json, and prints
    each amendment trigger.
    """
    settings = load_settings()
    for line in answers(settings, Path.cwd(), check=check, only_id=only):
        typer.echo(line)


@eval_app.command("preflight")
def eval_preflight_command() -> None:
    """Check both stores hold the same checked load before any query is scored.

    Runs, in order, the frozen set, the two load run records, the SQL state as
    the reader login, the graph state, and the schema fingerprint, and stops at
    the first that fails. Writes nothing.
    """
    settings = load_settings()
    typer.echo(summary(preflight(settings, Path.cwd())))


gold_app = typer.Typer(
    name="gold",
    help="Try, decide on and accept the gold queries of each question and store.",
    no_args_is_help=True,
)
eval_app.add_typer(gold_app)


@gold_app.command("try")
def gold_try_command(
    question: Annotated[str, typer.Argument(help="The question id, for example Q01.")],
    store: StoreOption,
    hypothesis: Annotated[
        str | None,
        typer.Option("--hypothesis", help="From try 2 of a round: what you think was wrong."),
    ] = None,
    cites: Annotated[
        str | None,
        typer.Option(
            "--cites",
            help="From try 2 of a round: `step <n>` of the reading note, or a schema name.",
        ),
    ] = None,
) -> None:
    """Run one gold pair on its store and score it against the frozen known answer.

    Runs the preflight first. Prints three booleans and the count of missing and
    extra IDs per source step, never an answer key, value or ID, and appends the
    try to results/gold/tries.jsonl. A refusal before the pair runs is not a try.
    """
    settings = load_settings()
    for line in gold_try(
        settings,
        Path.cwd(),
        question,
        parse_store(store),
        hypothesis=hypothesis,
        cites=cites,
    ):
        typer.echo(line)


@gold_app.command("decide")
def gold_decide_command(
    question: Annotated[str, typer.Argument(help="The question id, for example Q01.")],
    store: StoreOption,
    rewrite: Annotated[
        bool, typer.Option("--rewrite", help="Open a fresh round, written from the reading note.")
    ] = False,
    discrepancy: Annotated[
        str | None,
        typer.Option("--discrepancy", help="Record the pair as a known answer discrepancy."),
    ] = None,
) -> None:
    """After 3 failed tries in a round: open a new round, or record a known answer discrepancy.

    Reads no store; appends the choice to results/gold/tries.jsonl.
    """
    typer.echo(
        gold_decide(
            Path.cwd(), question, parse_store(store), rewrite=rewrite, discrepancy=discrepancy
        )
    )


@gold_app.command("accept")
def gold_accept_command(
    question: Annotated[str, typer.Argument(help="The question id, for example Q01.")],
    store: StoreOption,
    reviewed: Annotated[
        bool,
        typer.Option("--reviewed", help="You read every query against gold/CHECKLIST.md."),
    ] = False,
) -> None:
    """Pin a pair in gold/ACCEPTED once its latest try was correct for the files as they are.

    Also needs a clean literal check, the zero guard when it applies, an explain
    file naming every numbered source step, tries on the current load ID only,
    and --reviewed. Reads no store; logs the round and try the pair passed on.
    """
    for line in gold_accept(Path.cwd(), question, parse_store(store), reviewed=reviewed):
        typer.echo(line)


AgentOption = Annotated[
    str, typer.Option("--agent", help="replay, or module:factory returning a QueryAgent.")
]


@eval_app.command("run")
def eval_run_command(
    agent: AgentOption,
    estimate_only: Annotated[
        bool,
        typer.Option("--estimate", help="Write the version's PLAN.json and its cost bound only."),
    ] = False,
    go: Annotated[bool, typer.Option("--go", help="Allow a real agent's paid calls.")] = False,
    max_cost_usd: Annotated[
        float | None,
        typer.Option("--max-cost-usd", help="Stop starting runs once this invocation spends this."),
    ] = None,
    only: Annotated[
        str | None, typer.Option("--only", help="One question id, for example Q01.")
    ] = None,
    store: Annotated[
        str | None, typer.Option("--store", help="sql or graph; both when left out.")
    ] = None,
    max_run: Annotated[
        int | None, typer.Option("--max-run", min=1, help="Run only run numbers 1 to K.")
    ] = None,
    plain: Annotated[
        bool,
        typer.Option(
            "--plain", help="Run the plain series: the questions without their reading note."
        ),
    ] = False,
) -> None:
    """Run an agent on the questions in both stores and score every run.

    Runs the preflight first. Each run is written once to
    results/agent_runs/<version>/Qnn-<store>-<n>.json, and a rerun fills only the
    missing run numbers. A real agent needs `--estimate` first, a versions log
    entry, --go and --max-cost-usd. `--agent replay` runs the accepted gold
    queries for free and needs none of these. `--plain` runs the same version
    without the reading note into results/agent_runs/<version>/plain/, under
    its own plan and gate, and refuses replay (spec 0013).
    """
    settings = load_settings()
    if plain:
        if estimate_only:
            lines = estimate_plain(settings, Path.cwd(), agent, only=only)
        else:
            lines = run_plain_agent(
                settings,
                Path.cwd(),
                agent,
                go=go,
                max_cost_usd=max_cost_usd,
                only=only,
                store=store,
                max_run=max_run,
            )
    elif estimate_only:
        lines = estimate(settings, Path.cwd(), agent, only=only)
    else:
        lines = run_agent(
            settings,
            Path.cwd(),
            agent,
            go=go,
            max_cost_usd=max_cost_usd,
            only=only,
            store=store,
            max_run=max_run,
        )
    for line in lines:
        typer.echo(line)


version_app = typer.Typer(
    name="version",
    help="The agent versions log: at most 3 versions, append only.",
    no_args_is_help=True,
)
eval_app.add_typer(version_app)


@version_app.command("add")
def version_add_command(
    agent: AgentOption,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Why this version exists (after the first).")
    ] = None,
    exposed_by: Annotated[
        list[str] | None,
        typer.Option(
            "--exposed-by",
            help="A run file that exposed the problem; give it once per file.",
        ),
    ] = None,
    kind: Annotated[str | None, typer.Option("--kind", help="bug_fix or behaviour_change.")] = None,
) -> None:
    """Log the agent's current version and the parts that changed since the previous one."""
    settings = load_settings()
    typer.echo(
        version_add(
            settings,
            Path.cwd(),
            agent,
            reason=reason,
            exposed_by=tuple(exposed_by or ()),
            kind=kind,
        )
    )


load_app = typer.Typer(
    name="load",
    help="Load the corpus from the Mongo staging store into the stores.",
    no_args_is_help=True,
)
app.add_typer(load_app)


@load_app.command("sql")
def load_sql_command() -> None:
    """Load the 7 corpus projects into Postgres and build model_int and model.

    Runs dlt into raw, `dbt build` with every test, the load consistency check
    and the figure gate, then writes results/sql_load/run_record.json, pass or
    fail. Reads Mongo through the reader login only; never writes to Mongo.
    """
    settings = load_settings()
    typer.echo(load_sql(settings, Path.cwd()))


@load_app.command("graph")
def load_graph_command() -> None:
    """Load model_int into Neo4j, restart it read only, and check it against model_int.

    Needs an `ok` SQL run record whose load ID matches the live tables. Makes
    Neo4j writable through docker compose, wipes and reloads it, makes it read
    only again (also after a failure or Ctrl+C), reconciles the counts, then
    writes results/graph_load/run_record.json. Run it from the repository root.
    Reads Postgres as the owner login only; never touches Mongo.
    """
    settings = load_settings()
    typer.echo(load_graph(settings, Path.cwd()))


def run(args: Sequence[str] | None = None) -> None:
    """Run the CLI, turning named errors into one stderr message and their exit code."""
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    try:
        app(args=args)
    except NAMED_ERRORS as error:
        typer.echo(str(error), err=True)
        code = next(code for named, code in EXIT_CODES if isinstance(error, named))
        raise SystemExit(code) from None
