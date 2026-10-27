"""The `orgos` command line entry point.

`app` holds the commands; `run()` is the script entry. Each command loads
`Settings` itself, so help never needs a `.env`. `run()` is the one place
that prints known errors and picks exit codes, through `EXIT_CODES`; a later
feature adds its command and one line there.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import typer

from orgos import __version__
from orgos.config import ConfigError, load_settings
from orgos.sqlstore import SqlStoreError
from orgos.sqlstore.bootstrap import bootstrap

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(levelname)s %(message)s"

# Named errors and their exit codes, checked in order. Anything else keeps its
# traceback. Click already uses 2 for a usage error.
EXIT_CODES: tuple[tuple[type[Exception], int], ...] = (
    (ConfigError, 2),
    (SqlStoreError, 1),
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


def run(args: Sequence[str] | None = None) -> None:
    """Run the CLI, turning named errors into one stderr message and their exit code."""
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    try:
        app(args=args)
    except NAMED_ERRORS as error:
        typer.echo(str(error), err=True)
        code = next(code for named, code in EXIT_CODES if isinstance(error, named))
        raise SystemExit(code) from None
