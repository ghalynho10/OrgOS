"""Fast checks that need no database."""

import re
import socket
import tomllib
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

import orgos.sqlstore.bootstrap
from orgos import __version__
from orgos.cli import app, run
from orgos.config import ConfigError, Settings, load_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Importing dbt loads `.env` into os.environ, so clear every setting first.
    for field in Settings.model_fields.values():
        monkeypatch.delenv(str(field.validation_alias), raising=False)


REQUIRED = {
    "POSTGRES_PASSWORD": "super-secret",
    "ORGOS_PG_OWNER_PASSWORD": "owner-secret",
    "ORGOS_PG_READER_PASSWORD": "reader-secret",
    "MONGO_INITDB_ROOT_PASSWORD": "mongo-secret",
    "NEO4J_AUTH": "neo4j/graph-secret",
}


def test_missing_config_names_every_missing_variable() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(_env_file=None)
    message = str(excinfo.value)
    for name in REQUIRED:
        assert f"{name} is not set" in message


def test_bad_neo4j_auth_is_rejected() -> None:
    with pytest.raises(ConfigError, match="NEO4J_AUTH"):
        load_settings(_env_file=None, **{**REQUIRED, "NEO4J_AUTH": "no-slash"})


def test_connection_strings_are_built_from_parts() -> None:
    settings = load_settings(_env_file=None, **REQUIRED, ORGOS_PG_PORT=55432)
    reader = settings.pg_conninfo("reader")
    assert "port=55432" in reader
    assert "user=orgos_reader" in reader
    assert "password=reader-secret" in reader
    assert settings.mongo_uri.endswith("@127.0.0.1:27017/?authSource=admin")
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password.get_secret_value() == "graph-secret"
    assert settings.dbt_env()["ORGOS_PG_PORT"] == "55432"


# covers: DW-3 (one config for every store connection)
def test_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ORGOS_PG_HOST", "db.internal")
    settings = load_settings(_env_file=None)
    assert settings.pg_host == "db.internal"
    assert settings.pg_reader_password.get_secret_value() == "reader-secret"


def test_settings_are_read_from_an_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{name}={value}\n" for name, value in REQUIRED.items()))
    settings = load_settings(_env_file=env_file)
    assert settings.neo4j_user == "neo4j"


def test_empty_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    with pytest.raises(ConfigError, match="POSTGRES_PASSWORD is not set"):
        load_settings(_env_file=None)


def test_spec_defaults_apply_when_unset() -> None:
    settings = load_settings(_env_file=None, **REQUIRED)
    assert settings.query_timeout_s == 30
    assert settings.agent_max_rows == 200
    assert settings.pg_database == "orgos"
    assert settings.pg_owner_user == "orgos_owner"
    assert settings.pg_reader_user == "orgos_reader"
    assert settings.neo4j_uri == "bolt://127.0.0.1:7687"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORGOS_PG_PORT", "not-a-port"),
        ("ORGOS_QUERY_TIMEOUT_S", "0"),
        ("ORGOS_AGENT_MAX_ROWS", "0"),
        ("ORGOS_AGENT_MAX_ROWS", "-5"),
    ],
)
def test_invalid_values_name_the_variable(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError, match=name):
        load_settings(_env_file=None, **REQUIRED)


@pytest.mark.parametrize("auth", ["neo4j/", "/graph-secret", "neo4j", ""])
def test_neo4j_auth_needs_both_user_and_password(auth: str) -> None:
    with pytest.raises(ConfigError, match="NEO4J_AUTH"):
        load_settings(_env_file=None, **{**REQUIRED, "NEO4J_AUTH": auth})


def test_neo4j_password_may_contain_a_slash() -> None:
    settings = load_settings(_env_file=None, **{**REQUIRED, "NEO4J_AUTH": "neo4j/pa/ss"})
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password.get_secret_value() == "pa/ss"


def test_mongo_uri_escapes_special_characters() -> None:
    settings = load_settings(
        _env_file=None,
        **{
            **REQUIRED,
            "MONGO_INITDB_ROOT_USERNAME": "us@er",
            "MONGO_INITDB_ROOT_PASSWORD": "p@ss:w/rd?",
        },
    )
    assert settings.mongo_uri == (
        "mongodb://us%40er:p%40ss%3Aw%2Frd%3F@127.0.0.1:27017/?authSource=admin"
    )


@pytest.mark.parametrize(
    ("role", "user", "password"),
    [
        ("superuser", "postgres", "super-secret"),
        ("owner", "orgos_owner", "owner-secret"),
        ("reader", "orgos_reader", "reader-secret"),
    ],
)
def test_pg_conninfo_uses_each_roles_own_credentials(role: str, user: str, password: str) -> None:
    settings = load_settings(_env_file=None, **REQUIRED)
    parts = conninfo_to_dict(settings.pg_conninfo(role))
    assert parts["user"] == user
    assert parts["password"] == password
    assert parts["dbname"] == "orgos"


def test_pg_conninfo_survives_awkward_passwords_and_dbname_override() -> None:
    awkward = "it's a pass word = \\ "
    settings = load_settings(_env_file=None, **{**REQUIRED, "ORGOS_PG_READER_PASSWORD": awkward})
    parts = conninfo_to_dict(settings.pg_conninfo("reader", dbname="postgres"))
    assert parts["password"] == awkward
    assert parts["dbname"] == "postgres"


def test_dbt_env_carries_only_the_owner_password() -> None:
    settings = load_settings(_env_file=None, **REQUIRED)
    values = settings.dbt_env().values()
    assert "owner-secret" in values
    assert "super-secret" not in values
    assert "reader-secret" not in values


def test_secrets_never_show_in_settings_repr() -> None:
    settings = load_settings(_env_file=None, **REQUIRED, ANTHROPIC_API_KEY="sk-test-key")
    shown = repr(settings) + str(settings)
    for secret in [*REQUIRED.values(), "graph-secret", "sk-test-key"]:
        assert secret not in shown


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The bootstrap reads `.env` from the working directory; keep the real one out.
    monkeypatch.chdir(tmp_path)


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def _run(args: list[str]) -> int | str | None:
    with pytest.raises(SystemExit) as excinfo:
        run(args)
    return excinfo.value.code


@pytest.mark.usefixtures("_no_env_file")
@pytest.mark.parametrize("args", [["--help"], ["bootstrap", "--help"]])
def test_help_needs_no_config(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(args) == 0
    assert "bootstrap" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["--version"]) == 0
    assert capsys.readouterr().out == f"orgos {__version__}\n"


@pytest.mark.parametrize("args", [["no-such-command"], ["--no-such-option"]])
def test_usage_errors_exit_2(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(args) == 2
    err = capsys.readouterr().err
    assert "Usage: " in err
    assert "No such " in err


@pytest.mark.usefixtures("_no_env_file")
def test_bootstrap_exits_2_on_missing_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(["bootstrap"]) == 2
    err = capsys.readouterr().err
    assert "POSTGRES_PASSWORD is not set" in err
    assert "Traceback" not in err


@pytest.mark.usefixtures("_no_env_file")
def test_config_error_does_not_echo_the_bad_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("NEO4J_AUTH", "leaked-secret-no-slash")
    assert _run(["bootstrap"]) == 2
    err = capsys.readouterr().err
    assert "NEO4J_AUTH" in err
    assert "leaked-secret-no-slash" not in err


@pytest.mark.usefixtures("_no_env_file")
def test_missing_config_names_the_folder_without_a_env_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(["bootstrap"]) == 2
    err = capsys.readouterr().err
    assert err.endswith(f"\n  no .env found in {tmp_path.resolve()}\n")


def test_folder_hint_names_an_explicit_env_files_folder(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(_env_file=tmp_path / "elsewhere" / ".env")
    assert str(excinfo.value).endswith(f"  no .env found in {tmp_path.resolve() / 'elsewhere'}")


@pytest.mark.parametrize("env_file", [None, ["a.env", "b.env"]])
def test_no_folder_hint_when_no_single_env_file_is_read(env_file: object) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(_env_file=env_file)
    assert "no .env found" not in str(excinfo.value)


@pytest.mark.usefixtures("_no_env_file")
def test_no_folder_hint_for_invalid_values_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("ORGOS_PG_PORT", "not-a-port")
    assert _run(["bootstrap"]) == 2
    err = capsys.readouterr().err
    assert "ORGOS_PG_PORT" in err
    assert "no .env found" not in err


def test_no_folder_hint_when_the_env_file_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=super-secret\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert "ORGOS_PG_OWNER_PASSWORD is not set" in message
    assert "POSTGRES_PASSWORD" not in message
    assert "no .env found" not in message


# Environment only, no `.env`: config loads, so the exit is Postgres's 1, not 2.
@pytest.mark.usefixtures("_no_env_file")
def test_bootstrap_exits_1_when_postgres_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_required(monkeypatch)
    port = _free_port()
    monkeypatch.setenv("ORGOS_PG_PORT", str(port))
    assert _run(["bootstrap"]) == 1
    err = capsys.readouterr().err
    assert f"Could not reach Postgres at 127.0.0.1:{port}" in err
    assert "Traceback" not in err
    for secret in REQUIRED.values():
        assert secret not in err


@pytest.mark.usefixtures("_no_env_file")
def test_unnamed_errors_keep_their_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(settings: Settings) -> None:
        raise RuntimeError("not in the exit code table")

    _set_required(monkeypatch)
    monkeypatch.setattr("orgos.cli.bootstrap", broken)
    with pytest.raises(RuntimeError, match="not in the exit code table"):
        run(["bootstrap"])


def test_tracebacks_never_show_locals() -> None:
    assert app.pretty_exceptions_show_locals is False


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "orgos"
# Reading the environment or writing a connection string belongs in `orgos.config` only.
CONFIG_LEAK = re.compile(
    r"\benviron\b|\bgetenv\b|postgres(ql)?://|mongodb(\+srv)?://|bolt://|neo4j(\+s)?://"
)


def config_leaks(root: Path) -> tuple[Path, ...]:
    """Files under `root`, outside its `config` folder, that match `CONFIG_LEAK`."""
    return tuple(
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and not path.is_relative_to(root / "config")
        and CONFIG_LEAK.search(path.read_text(encoding="utf-8", errors="ignore"))
    )


def test_only_config_reads_the_environment_or_builds_connection_strings() -> None:
    assert config_leaks(SRC) == ()


@pytest.mark.parametrize(
    "line",
    [
        'password = os.environ["POSTGRES_PASSWORD"]',
        'host = os.getenv("ORGOS_PG_HOST")',
        'url = "postgresql://user:pass@127.0.0.1/orgos"',
        'url = "postgres://user:pass@127.0.0.1/orgos"',
        'url = "mongodb+srv://cluster.example"',
        'url = "bolt://127.0.0.1:7687"',
        'url = "neo4j+s://graph.example"',
    ],
)
def test_config_scan_finds_a_planted_leak(tmp_path: Path, line: str) -> None:
    planted = tmp_path / "sqlstore" / "leak.py"
    planted.parent.mkdir()
    planted.write_text(line + "\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "__init__.py").write_text(line + "\n")
    assert config_leaks(tmp_path) == (planted,)


def test_the_old_bootstrap_entry_point_is_gone() -> None:
    module = orgos.sqlstore.bootstrap
    assert not hasattr(module, "main")
    assert "__main__" not in Path(module.__file__ or "").read_text(encoding="utf-8")
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"orgos": "orgos.cli:run"}
    for doc in ("README.md", "AGENTS.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "python -m orgos.sqlstore.bootstrap" not in text
        assert "uv run orgos bootstrap" in text
