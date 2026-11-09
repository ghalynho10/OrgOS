"""The graph store (Neo4j): the load from `model_int` and safe query access.

`orgos load graph` (spec 0010) reads the 12 `model_int` tables, makes Neo4j
writable, wipes and reloads it through the mapping file, restarts it read only
and checks the result. Every failure the load can expect is a
`GraphStoreError`, which the CLI turns into exit code 6.
"""


class GraphStoreError(Exception):
    """Raised when the graph load refuses to start, a step fails, or a check fails."""


# Measured in the spec 0010 trial (rationale.md), not tuned since.
WRITE_BATCH_ROWS = 5_000
WIPE_BATCH_ROWS = 10_000
RETRY_SECONDS = 5.0
WAIT_SECONDS = 120
# How many keys or `edge_id` values a problem names at most (AC-12, AC-18).
SHOWN = 20

# The Neo4j service in docker-compose.yml and the one variable a load sets.
COMPOSE_PROFILE = "graph"
COMPOSE_SERVICE = "neo4j"
READ_ONLY_VARIABLE = "ORGOS_NEO4J_READ_ONLY_DATABASES"
DATABASE = "neo4j"
