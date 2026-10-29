"""The `orgos` command line entry point.

`app` holds the commands; `run()` is the script entry. Each command loads
`Settings` itself, so help never needs a `.env`. `run()` is the one place
that prints known errors and picks exit codes, through `EXIT_CODES`; a later
feature adds its command and one line there.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from orgos import __version__
from orgos.config import ConfigError, load_settings
from orgos.eval import EvalError
from orgos.eval.checker import check_files
from orgos.mongostore import MongoStoreError
from orgos.mongostore.restore import restore
from orgos.mongostore.verify import verify
from orgos.sqlstore import SqlStoreError
from orgos.sqlstore.bootstrap import bootstrap

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(levelname)s %(message)s"

# Named errors and their exit codes, checked in order. Anything else keeps its
# traceback. Click already uses 2 for a usage error.
EXIT_CODES: tuple[tuple[type[Exception], int], ...] = (
    (ConfigError, 2),
    (SqlStoreError, 1),
    (MongoStoreError, 3),
    (EvalError, 4),
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


def run(args: Sequence[str] | None = None) -> None:
    """Run the CLI, turning named errors into one stderr message and their exit code."""
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    try:
        app(args=args)
    except NAMED_ERRORS as error:
        typer.echo(str(error), err=True)
        code = next(code for named, code in EXIT_CODES if isinstance(error, named))
        raise SystemExit(code) from None
