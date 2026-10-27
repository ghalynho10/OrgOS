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

import time
import uuid

import docker
import pytest
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from testcontainers.community.mongodb import MongoDbContainer
from testcontainers.community.neo4j import Neo4jContainer
from testcontainers.community.postgres import PostgresContainer

from orgos.config import Settings, load_settings

POSTGRES_IMAGE = "postgres:18.6"
NEO4J_IMAGE = "neo4j:2026.10.1-community"
MONGO_IMAGE = "mongo:7.0.45"

SUPERUSER_PASSWORD = "test-superuser"
NEO4J_PASSWORD = "test-graph-password"
MONGO_ROOT_USER = "orgos"
MONGO_ROOT_PASSWORD = "test-mongo-root"


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
        ORGOS_MONGO_READER_PASSWORD="unused",
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


@pytest.fixture(scope="session")
def mongo() -> Iterator[MongoDbContainer]:
    """Mongo with root auth on, like the compose service.

    The image's init step logs "waiting for connections" for a local only
    server first, so wait until the root login works from the host.
    """
    container = MongoDbContainer(
        MONGO_IMAGE, username=MONGO_ROOT_USER, password=MONGO_ROOT_PASSWORD
    ).with_command("mongod --wiredTigerCacheSizeGB 0.25")
    with container:
        deadline = time.monotonic() + 60
        while True:
            try:
                with MongoClient(
                    container.get_connection_url(), serverSelectionTimeoutMS=1000
                ) as client:
                    client.admin.command("listDatabases")
                break
            except PyMongoError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield container
