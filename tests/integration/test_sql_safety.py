"""The Postgres reader role can read `model` and nothing else, and cannot write."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from dbt.cli.main import dbtRunner
from pydantic import SecretStr

from orgos.config import Settings
from orgos.sqlstore.bootstrap import SCHEMAS, bootstrap

pytestmark = pytest.mark.integration

DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"


def _dbt(*args: str) -> None:
    result = dbtRunner().invoke(
        [*args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    )
    assert result.success, result.exception


@pytest.fixture(scope="module")
def built(pg_settings: Settings, monkeypatch_module: pytest.MonkeyPatch) -> Settings:
    # Twice, to prove the bootstrap is idempotent.
    bootstrap(pg_settings)
    bootstrap(pg_settings)

    for name, value in pg_settings.dbt_env().items():
        monkeypatch_module.setenv(name, value)
    probes = "{orgos_scaffold_probes: true}"
    # Twice, so the second run rebuilds tables that already exist (grants dropped).
    _dbt("run", "--full-refresh", "--vars", probes)
    _dbt("run", "--full-refresh", "--vars", probes)

    with psycopg.connect(pg_settings.pg_conninfo("owner"), autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS raw.probe AS SELECT 1 AS probe_id")
        # A table the owner creates outside dbt still gets SELECT by default privileges.
        conn.execute("DROP TABLE IF EXISTS model.plain_probe")
        conn.execute("CREATE TABLE model.plain_probe AS SELECT 1 AS probe_id")
    return pg_settings


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture
def reader(built: Settings) -> Iterator[psycopg.Connection]:
    with psycopg.connect(built.pg_conninfo("reader"), autocommit=True) as conn:
        yield conn


def test_schemas_have_exact_names(built: Settings) -> None:
    with psycopg.connect(built.pg_conninfo("owner")) as conn:
        rows = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'scaffold_probe_%' AND table_type = 'BASE TABLE' ORDER BY 1"
        ).fetchall()
    assert rows == [
        ("model", "scaffold_probe_model"),
        ("model_int", "scaffold_probe_model_int"),
    ]
    with psycopg.connect(built.pg_conninfo("owner")) as conn:
        views = conn.execute(
            "SELECT table_schema FROM information_schema.views WHERE table_name = 'scaffold_probe_stg'"
        ).fetchall()
    assert views == [("stg",)]


@pytest.mark.parametrize("table", ["model.scaffold_probe_model", "model.plain_probe"])
def test_reader_can_select_model_after_full_refresh(reader: psycopg.Connection, table: str) -> None:
    assert reader.execute(f"SELECT probe_id FROM {table}").fetchone() == (1,)


@pytest.mark.parametrize(
    "table", ["model_int.scaffold_probe_model_int", "stg.scaffold_probe_stg", "raw.probe"]
)
def test_reader_cannot_read_other_schemas(reader: psycopg.Connection, table: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute(f"SELECT * FROM {table}")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO model.scaffold_probe_model VALUES (2)",
        "UPDATE model.scaffold_probe_model SET probe_id = 3",
        "DELETE FROM model.scaffold_probe_model",
        "CREATE TABLE model.reader_table (x int)",
        "CREATE TABLE public.reader_table (x int)",
        "CREATE TEMP TABLE reader_temp (x int)",
        "DROP TABLE model.scaffold_probe_model",
    ],
)
def test_reader_cannot_write(reader: psycopg.Connection, statement: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute(statement)


def test_reader_limits_are_set_by_role(reader: psycopg.Connection) -> None:
    assert reader.execute("SHOW statement_timeout").fetchone() == ("5s",)
    assert reader.execute("SHOW temp_file_limit").fetchone() == ("1GB",)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reader.execute("SET temp_file_limit = -1")


# covers: SC-6 (schemas exist, owned by the owner role)
def test_schemas_are_owned_by_the_owner_role(built: Settings) -> None:
    with psycopg.connect(built.pg_conninfo("owner")) as conn:
        rows = conn.execute(
            "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace "
            "WHERE nspname = ANY(%s) ORDER BY 1",
            (list(SCHEMAS),),
        ).fetchall()
    assert rows == sorted((schema, built.pg_owner_user) for schema in SCHEMAS)


def test_reader_work_mem_is_capped_by_role(reader: psycopg.Connection) -> None:
    assert reader.execute("SHOW work_mem").fetchone() == ("16MB",)


def test_rerun_resets_the_reader_password_and_timeout(pg_settings: Settings) -> None:
    # Its own database and roles, so the module's `built` state is untouched.
    first = pg_settings.model_copy(
        update={
            "pg_database": "orgos_rerun",
            "pg_owner_user": "rerun_owner",
            "pg_reader_user": "rerun_reader",
            "pg_reader_password": SecretStr("first-password"),
        }
    )
    second = first.model_copy(
        update={"pg_reader_password": SecretStr("second-password"), "query_timeout_s": 7}
    )
    bootstrap(first)
    bootstrap(second)

    with psycopg.connect(second.pg_conninfo("reader"), autocommit=True) as conn:
        assert conn.execute("SHOW statement_timeout").fetchone() == ("7s",)
    with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
        psycopg.connect(first.pg_conninfo("reader"))
