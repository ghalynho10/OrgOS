"""The `orgos` command line entry point.

Commands are designed in the CLI shell spec (scope feature 3); this is only
the root they hang off.
"""

import typer

from orgos import __version__

app = typer.Typer(
    name="orgos",
    help="orgOS: query one company's work data through a SQL store and a graph store.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"orgos {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", help="Show the version and exit.", callback=_print_version, is_eager=True
    ),
) -> None:
    """orgOS: query one company's work data through a SQL store and a graph store."""
