# graphstore

## Overview

The graph store (Neo4j). `uv run orgos load graph` reads the 12 `model_int` tables, makes Neo4j writable, wipes and reloads it through `mapping.toml`, restarts it read only, then checks it and compares it with `model_int` row by row. Spec 0008 is the shape of the graph; spec 0010 is the load, its checks and its run record (`results/graph_load/run_record.json`). Every expected failure is a `GraphStoreError` (exit 6).

## The graph

| `model_int` table | Label | Key (unique constraint) |
| --- | --- | --- |
| `issue` | `Issue` | `issueKey` (`issue_issuekey_unique`) |
| `project` | `Project` | `projectKey` (`project_projectkey_unique`) |
| `person` | `Person` | `personKey` (`person_personkey_unique`) |
| `fix_version` | `FixVersion` | `versionId` (`fixversion_versionid_unique`) |

| `model_int` table | Relationship type | Direction |
| --- | --- | --- |
| `in_project` | `IN_PROJECT` | `Issue` to `Project` |
| `assigned_to` | `ASSIGNED_TO` | `Issue` to `Person` |
| `reported_by` | `REPORTED_BY` | `Issue` to `Person` |
| `created_by` | `CREATED_BY` | `Issue` to `Person` |
| `has_parent` | `HAS_PARENT` | child `Issue` to parent `Issue` |
| `fixed_in` | `FIXED_IN` | `Issue` to `FixVersion` |
| `logged_time` | `LOGGED_TIME` (`historyId`, `seconds`, `loggedAt`) | `Person` to `Issue` |
| `issue_link` | 17 types, one per `edge_type`: `BLOCKER`, `BONFIRE_TESTING`, `CAUSE`, `CLONERS`, `DEPENDS`, `DERIVED`, `DETAIL`, `DUPLICATE`, `FOLLOWS`, `PART`, `REFERENCE`, `REGRESSION`, `RELATE`, `RELATED`, `RESOLVE`, `SPLIT`, `SUPERSESSION` (`linkId`, `outwardLabel`, `inwardLabel`) | outward `Issue` to inward `Issue` |

Every node and relationship has `sourceDocId` and `loadId`; every relationship has `edgeId`. The only indexes are the four the constraints own, plus Neo4j's two default lookup indexes.

## Key files

| File | Owns |
| --- | --- |
| `mapping.toml` | Tables to labels, types and properties; the loader holds no name of its own |
| `mapping.py` | Reads and checks the mapping; the contract checks against `dbt/models/model_int/model_int.yml` |
| `convert.py` | Row values to property values (pure): nulls absent, every datetime `astimezone(UTC)` |
| `statements.py` | Every Cypher statement, built from the mapping, each starting `CYPHER 25` |
| `checks.py` | "The graph is in the expected state": `read_graph_state`, `graph_schema_problems`, `graph_state_problems`, `check_run_records`, reconciliation, temporal check |
| `compare.py` | The value level compare of every node and relationship with its `model_int` row |
| `compose.py` | The read only toggle through `docker compose` and its `SHOW DATABASES` poll |
| `load.py` | The command: preflight, steps, restore, checks, run record |
| `record.py` | The run record, `results/graph_load/run_record.json` (spec 0010 AC-6, AC-21) |

## Conventions

- Pure functions judge, `read_*` functions touch Neo4j. Features 12 and 13 call `read_graph_state`, `graph_state_problems` and `check_run_records` in their preflight; never write a second copy.
- A label, type or property name goes into Cypher only through `statements.name`, which checks it against a plain identifier pattern; every value is a parameter.
- Every datetime is stored in the `Z` form. A query bound written with a named zone misjudges an exact tie (spec 0008 AC-17).
- The record is `fail` from before Neo4j is first made writable until the last act of a passed run; read the record, not the constraints, to know the graph is good.

## Gotchas

- Run from the repo root: the toggle runs `docker compose` there, with `ORGOS_NEO4J_READ_ONLY_DATABASES` set for that child process only.
- Neo4j needs a clean stop before a read only start. After an unclean stop (a killed container) the database needs recovery, a read only start cannot run it, and `neo4j` never comes online; the integration tests stop their containers cleanly for this reason.
- The wipe uses `CALL ... IN TRANSACTIONS`, which needs an autocommit session (`session.run`), not `execute_query` or `execute_write`.
- Counting a relationship type with no rows by name logs a driver warning; `COUNT_BY_TYPE` counts every type in one grouped read instead.
- `dbms.components()` returns two rows on this image; filter on `name = 'Neo4j Kernel'`.

## Related specs

- [0008 graph model](../../../docs/specs/0008-graph-model/index.md): labels, types, properties, constraints
- [0010 graph load](../../../docs/specs/0010-graph-load/index.md): the load, its checks, tests and run record
