"""Fast checks that need no database."""

import pytest
from typer.testing import CliRunner

from orgos import __version__
from orgos.cli import app
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
