"""Make Neo4j writable for a load, and read only again, through `docker compose`.

Spec 0010 AC-4, AC-7 and AC-8. Neo4j reads `server.databases.read_only` only
at start, so the load recreates the container: `docker compose up` with
`ORGOS_NEO4J_READ_ONLY_DATABASES` set for that child process only (empty for
writable, `neo4j` for read only), then polls `SHOW DATABASES` until `neo4j` is
online with the wanted access. The command builders are pure; `set_access` and
`docker_problems` are the shell.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase
from neo4j.exceptions import DatabaseUnavailable, ServiceUnavailable, SessionExpired, TransientError

from orgos.config import Settings, compose_subprocess_env
from orgos.graphstore import (
    COMPOSE_PROFILE,
    COMPOSE_SERVICE,
    DATABASE,
    READ_ONLY_VARIABLE,
    WAIT_SECONDS,
    GraphStoreError,
)
from orgos.graphstore.statements import SHOW_DATABASE

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 1.0
# `docker compose version` passes with the daemon stopped, so `docker info` is
# checked too (AC-4).
DOCKER_CHECKS: tuple[tuple[str, ...], ...] = (("docker", "info"), ("docker", "compose", "version"))
# Errors Neo4j gives while it is starting; the poll waits through them.
STARTING_ERRORS = (ServiceUnavailable, SessionExpired, DatabaseUnavailable, TransientError)


def compose_command() -> tuple[str, ...]:
    """The `docker compose up` that recreates the Neo4j service and waits until it is healthy."""
    return (
        "docker",
        "compose",
        "--profile",
        COMPOSE_PROFILE,
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        str(WAIT_SECONDS),
        COMPOSE_SERVICE,
    )


def read_only_value(read_only: bool) -> str:
    """The value of `ORGOS_NEO4J_READ_ONLY_DATABASES`: `neo4j` for read only, empty for writable."""
    return DATABASE if read_only else ""


def access_name(read_only: bool) -> str:
    """The `access` that `SHOW DATABASES` reports for the wanted state."""
    return "read-only" if read_only else "read-write"


def docker_problems(root: Path) -> tuple[str, ...]:
    """Each Docker check that fails: the daemon (`docker info`) and the compose plugin."""
    problems = []
    for command in DOCKER_CHECKS:
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True, check=False)
        except OSError as error:
            problems.append(f"`{' '.join(command)}` cannot run: {error.strerror}")
            continue
        if completed.returncode != 0:
            problems.append(f"`{' '.join(command)}` failed (exit code {completed.returncode})")
    return tuple(problems)


def _database_state(settings: Settings) -> tuple[str, str] | None:
    # The (access, status) of `neo4j`, or None while the server is still starting.
    auth = (settings.neo4j_user, settings.neo4j_password.get_secret_value())
    try:
        with GraphDatabase.driver(settings.neo4j_uri, auth=auth) as driver:
            records = driver.execute_query(SHOW_DATABASE, name=DATABASE, database_="system").records
    except STARTING_ERRORS:
        return None
    if not records:
        return None
    return str(records[0]["access"]), str(records[0]["currentStatus"])


def set_access(settings: Settings, root: Path, *, read_only: bool) -> None:
    """Recreate Neo4j with the wanted access and wait until `neo4j` is online with it.

    Raises `GraphStoreError` when compose fails or the database is not online
    with that access within 120 seconds.
    """
    wanted = access_name(read_only)
    log.info("recreating Neo4j %s", wanted)
    completed = subprocess.run(
        compose_command(),
        cwd=root,
        env=compose_subprocess_env(READ_ONLY_VARIABLE, read_only_value(read_only)),
        stdout=sys.stderr,
        check=False,
    )
    if completed.returncode != 0:
        raise GraphStoreError(f"docker compose up failed (exit code {completed.returncode})")
    deadline = time.monotonic() + WAIT_SECONDS
    state = None
    while time.monotonic() < deadline:
        state = _database_state(settings)
        if state == (wanted, "online"):
            log.info("Neo4j database %s is online and %s", DATABASE, wanted)
            return
        time.sleep(POLL_INTERVAL_S)
    seen = "unreachable" if state is None else f"{state[1]} and {state[0]}"
    raise GraphStoreError(
        f"Neo4j database {DATABASE} was not online and {wanted} within {WAIT_SECONDS} s"
        f" (last seen: {seen})"
    )
