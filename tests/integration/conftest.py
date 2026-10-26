"""Real store containers for the integration tests.

These start their own small containers, so stop the compose stack first
(`docker compose --profile all down`) to leave enough Docker memory.
Image tags match `docker-compose.yml`.
"""

import os
from collections.abc import Iterator

# Docker Desktop's host socket (~/.docker/run/docker.sock) cannot be mounted
# into the cleanup container; the path inside the Docker VM can. Set before
# testcontainers is imported, since it reads its config at import time.
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

import uuid  # noqa: E402

import docker  # noqa: E402
import pytest  # noqa: E402
from testcontainers.community.neo4j import Neo4jContainer  # noqa: E402
from testcontainers.community.postgres import PostgresContainer  # noqa: E402

from orgos.config import Settings, load_settings  # noqa: E402

POSTGRES_IMAGE = "postgres:18.6"
NEO4J_IMAGE = "neo4j:2026.10.1-community"

SUPERUSER_PASSWORD = "test-superuser"
NEO4J_PASSWORD = "test-graph-password"


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    container = PostgresContainer(
        POSTGRES_IMAGE, username="postgres", password=SUPERUSER_PASSWORD, dbname="postgres"
    )
    with container:
        yield container


@pytest.fixture(scope="session")
def pg_settings(postgres: PostgresContainer) -> Settings:
    return load_settings(
        _env_file=None,
        ORGOS_PG_HOST=postgres.get_container_host_ip(),
        ORGOS_PG_PORT=int(postgres.get_exposed_port(5432)),
        POSTGRES_PASSWORD=SUPERUSER_PASSWORD,
        ORGOS_PG_OWNER_PASSWORD="test-owner",
        ORGOS_PG_READER_PASSWORD="test-reader",
        MONGO_INITDB_ROOT_PASSWORD="unused",
        NEO4J_AUTH="neo4j/unused-password",
        ORGOS_QUERY_TIMEOUT_S=5,
    )


def _neo4j(volume: str) -> Neo4jContainer:
    return (
        Neo4jContainer(NEO4J_IMAGE, password=NEO4J_PASSWORD)
        .with_env("NEO4J_server_memory_heap_max__size", "256m")
        .with_env("NEO4J_server_memory_pagecache_size", "64m")
        .with_volume_mapping(volume, "/data", "rw")
    )


@pytest.fixture(scope="session")
def neo4j_read_only() -> Iterator[Neo4jContainer]:
    """The graph load cycle: start writable, write, restart read only on the same volume.

    Neo4j cannot create a database while read only, so a fresh volume must
    start writable first. The probe node proves the data survives the restart.
    """
    volume = f"orgos-test-neo4j-{uuid.uuid4().hex[:8]}"
    try:
        with _neo4j(volume) as writable, writable.get_driver() as driver:
            driver.execute_query("CREATE (:Probe {id: 1})", database_="neo4j")
        with _neo4j(volume).with_env("NEO4J_server_databases_read__only", "neo4j") as container:
            yield container
    finally:
        docker.from_env().volumes.get(volume).remove(force=True)
