"""Fast checks that need no database."""

import socket
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict
from typer.testing import CliRunner

from orgos import __version__
from orgos.cli import app
from orgos.config import ConfigError, Settings, load_settings
from orgos.sqlstore.bootstrap import main as bootstrap_main


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


def test_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "orgos" in result.output.lower()


def test_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


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


def test_unknown_option_fails_with_usage_error() -> None:
    result = CliRunner().invoke(app, ["--no-such-option"])
    assert result.exit_code == 2
    assert "No such option" in result.output


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
        **{**REQUIRED, "MONGO_INITDB_ROOT_USERNAME": "us@er", "MONGO_INITDB_ROOT_PASSWORD": "p@ss:w/rd?"},
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


def test_config_error_does_not_echo_the_bad_value() -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(_env_file=None, **{**REQUIRED, "NEO4J_AUTH": "leaked-secret-no-slash"})
    assert "leaked-secret-no-slash" not in str(excinfo.value)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The bootstrap reads `.env` from the working directory; keep the real one out.
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("_no_env_file")
def test_bootstrap_exits_2_on_missing_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert bootstrap_main() == 2
    assert "POSTGRES_PASSWORD is not set" in capsys.readouterr().err


@pytest.mark.usefixtures("_no_env_file")
def test_bootstrap_exits_1_when_postgres_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    port = _free_port()
    monkeypatch.setenv("ORGOS_PG_PORT", str(port))
    assert bootstrap_main() == 1
    err = capsys.readouterr().err
    assert f"Could not reach Postgres at 127.0.0.1:{port}" in err
    assert "super-secret" not in err
