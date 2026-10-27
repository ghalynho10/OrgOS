"""Idempotent Postgres bootstrap, run as the container superuser.

Creates the `orgos` database, the owner and reader roles, the four schemas,
and the grants that keep model written queries read only on `model`. Init
scripts only run on an empty volume, so this runs from OrgOS and is safe to
rerun: it creates what is missing and resets passwords, grants, and limits.

Run it with `uv run orgos bootstrap`.
"""

from __future__ import annotations

import logging

import psycopg
from psycopg import sql

from orgos.config import Settings
from orgos.sqlstore import SqlStoreError

log = logging.getLogger(__name__)

SCHEMAS = ("raw", "stg", "model_int", "model")
SERVED_SCHEMA = "model"

# Resource caps on the reader role. `temp_file_limit` can only be changed by
# a superuser, so a reader session cannot lift it.
READER_WORK_MEM = "16MB"
READER_TEMP_FILE_LIMIT = "1GB"


def _ensure_role(cur: psycopg.Cursor, name: str, password: str) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(
        sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(name), sql.Literal(password)
        )
    )


def bootstrap(settings: Settings) -> None:
    """Create or reset the database, roles, schemas, and grants; safe to rerun."""
    try:
        _bootstrap(settings)
    except psycopg.OperationalError as exc:
        raise SqlStoreError(
            f"Could not reach Postgres at {settings.pg_host}:{settings.pg_port}: {exc}"
        ) from exc
    log.info("bootstrap complete for database %s", settings.pg_database)


def _bootstrap(settings: Settings) -> None:
    owner = sql.Identifier(settings.pg_owner_user)
    reader = sql.Identifier(settings.pg_reader_user)
    database = sql.Identifier(settings.pg_database)

    superuser_maintenance = settings.pg_conninfo("superuser", dbname="postgres")
    with psycopg.connect(superuser_maintenance, autocommit=True) as conn, conn.cursor() as cur:
        _ensure_role(cur, settings.pg_owner_user, settings.pg_owner_password.get_secret_value())
        _ensure_role(cur, settings.pg_reader_user, settings.pg_reader_password.get_secret_value())

        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (settings.pg_database,))
        if not cur.fetchone():
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(database, owner))
            log.info("created database %s", settings.pg_database)

        cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database))
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, reader))

        timeout = f"{settings.query_timeout_s}s"
        for setting, value in (
            ("statement_timeout", timeout),
            ("work_mem", READER_WORK_MEM),
            ("temp_file_limit", READER_TEMP_FILE_LIMIT),
        ):
            cur.execute(
                sql.SQL("ALTER ROLE {} SET {} = {}").format(
                    reader, sql.Identifier(setting), sql.Literal(value)
                )
            )

    superuser_orgos = settings.pg_conninfo("superuser")
    with psycopg.connect(superuser_orgos, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        for schema in SCHEMAS:
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                    sql.Identifier(schema), owner
                )
            )
            cur.execute(
                sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(schema), owner)
            )
            cur.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(schema))
            )

        served = sql.Identifier(SERVED_SCHEMA)
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(served, reader))
        cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(served, reader))
        # dbt rebuilds drop grants, so new tables in `model` inherit SELECT.
        cur.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}"
            ).format(owner, served, reader)
        )
