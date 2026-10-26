"""Neo4j rejects writes while `server.databases.read_only` names the database."""

import pytest
from neo4j.exceptions import ClientError
from testcontainers.community.neo4j import Neo4jContainer

pytestmark = pytest.mark.integration


def test_read_only_setting_is_active(neo4j_read_only: Neo4jContainer) -> None:
    with neo4j_read_only.get_driver() as driver:
        records, _, _ = driver.execute_query(
            "SHOW SETTINGS YIELD name, value WHERE name = 'server.databases.read_only' "
            "RETURN value"
        )
    assert "neo4j" in records[0]["value"]


def test_create_is_rejected(neo4j_read_only: Neo4jContainer) -> None:
    with neo4j_read_only.get_driver() as driver:
        with pytest.raises(ClientError) as excinfo:
            driver.execute_query("CREATE (:Probe {id: 1})", database_="neo4j")
        assert "read-only" in (excinfo.value.message or "").lower()
        records, _, _ = driver.execute_query(
            "MATCH (n) RETURN count(n) AS nodes", database_="neo4j"
        )
    # Only the node written before the read only restart.
    assert records[0]["nodes"] == 1


def test_reads_still_work(neo4j_read_only: Neo4jContainer) -> None:
    with neo4j_read_only.get_driver() as driver:
        records, _, _ = driver.execute_query("RETURN 1 AS ok", database_="neo4j")
    assert records[0]["ok"] == 1
