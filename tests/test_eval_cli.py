"""Fast checks of the `orgos eval` group and its exit code (spec 0005)."""

import pytest
import typer

from orgos.cli import EXIT_CODES, app, run
from orgos.eval import EvalError


def test_exit_codes_hold_the_eval_row() -> None:
    assert (EvalError, 4) in EXIT_CODES
    assert sorted(code for _, code in EXIT_CODES) == [1, 2, 3, 4]


def test_an_eval_error_exits_4_once_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    probe = typer.Typer()

    @probe.command()
    def broken() -> None:
        raise EvalError("Q03 hops: must be an integer")

    monkeypatch.setattr("orgos.cli.app", probe)
    with pytest.raises(SystemExit) as exit_info:
        run([])
    assert exit_info.value.code == 4
    err = capsys.readouterr().err
    assert err.count("Q03 hops: must be an integer") == 1
    assert "Traceback" not in err


def test_the_eval_group_is_registered(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        app(args=["eval", "--help"])
    assert exit_info.value.code == 0
    assert "eval [OPTIONS] COMMAND" in capsys.readouterr().out
