# orgOS

A shared operational memory over a company's work data (projects, people, work, hours) that AI agents can query and cite. Phase 1 loads the same real Jira data into a SQL store (Postgres) and a graph store (Neo4j), then answers about 20 multi hop questions from both, so you can say plainly which question types the graph handles better and which it handles worse.

The project brief, scope, and design specs are kept locally in `docs/`, outside this repository.

## How it fits together

```
MongoDB (staging, raw dump, never edited)
   │  dlt (src/orgos/ingest)
   ▼
Postgres  raw ──dbt──▶ stg ──▶ model_int (entity layer) ──▶ model (served SQL tables)
                                   │
                                   │  graph load (src/orgos/graphstore)
                                   ▼
                                Neo4j (served graph)
```

Both served stores are built from the same `model_int` layer. The one query agent reads only `model` (as `orgos_reader`) or the read only Neo4j database.

## Setup

You need Docker Desktop, [uv](https://docs.astral.sh/uv/), and git. uv installs Python 3.12 for the project; your system Python does not matter.

1. **Install the package.**
   ```sh
   uv sync
   uv run orgos --help
   ```
2. **Create your `.env`.** Copy `.env.example` to `.env` and fill in every password. Compose and orgOS both read it. If a default port is taken on your machine (5432 and 7687 often are), change `ORGOS_PG_PORT`, `ORGOS_NEO4J_BOLT_PORT`, or `ORGOS_NEO4J_HTTP_PORT`.
3. **Bring up the stores.** One command starts all three and returns only when each is healthy:
   ```sh
   docker compose --profile all up -d --wait
   ```
   Profiles let you run less: `staging` (Mongo), `sql` (Postgres), `graph` (Neo4j). Use `staging` alone during the Mongo restore, so it has the memory to itself.
4. **Bootstrap Postgres** (creates the `orgos` database, roles, schemas, and grants; safe to rerun):
   ```sh
   uv run --env-file .env python -m orgos.sqlstore.bootstrap
   ```
5. **Initialize Neo4j once** on a fresh volume. Neo4j cannot create a database while it is read only, so the very first start must be writable. The graph load does this every time it runs; until the first load, you can do it yourself:
   ```sh
   ORGOS_NEO4J_READ_ONLY_DATABASES= docker compose --profile all up -d --wait neo4j   # writable
   docker compose --profile all up -d --wait neo4j                                    # back to read only
   ```
   Until then, `neo4j` shows as `offline` in `SHOW DATABASES`. The container is still healthy, since the healthcheck asks the `system` database.
6. **Check dbt can connect:**
   ```sh
   uv run --env-file .env dbt debug --project-dir dbt --profiles-dir dbt
   ```

### Docker disk and memory

- **Disk.** The Mongo restore expands to about 60 GB. Before restoring, check free space *inside Docker's virtual disk*, not on your Mac disk: `docker run --rm alpine:3.22 df -h /`. You need about 80 GB free. If you have less, raise the Docker Desktop disk limit (Settings, Resources) to at least 128 GB.
- **Memory.** The three containers are capped at 2.5 GB (Mongo), 1.5 GB (Postgres), and 2 GB (Neo4j), 6 GB in all. With 7.6 GB for Docker, stop other projects' containers before a restore or a load. Raising Docker's memory is cheaper than tuning.
- **Restore time.** The dataset README says about 15 minutes on an unconstrained machine; with a 1 GB WiredTiger cache, budget about an hour.

## Data

The raw data is never committed (`data/` is gitignored) and never edited.

| Item | Value |
|---|---|
| Source | The Public Jira Dataset, Zenodo record 15719919 |
| Version | 2025-06-23 |
| URL | https://zenodo.org/records/15719919/files/2025-06-23%20ThePublicJiraDataset.zip?download=1 |
| Local file | `data/2025-06-23 ThePublicJiraDataset.zip` (the name has a space; quote it) |
| md5 of the zip | `02f85309d966092ea130ca0797aea795` |
| Dump inside | `ThePublicJiraDataset/3. DataDump/mongodump-JiraReposAnon.archive` (gzip archive, database `JiraReposAnon`) |
| Dumped with | MongoDB server 7.0.14, database tools 100.9.5 (read from the archive header) |
| License | CC BY 4.0 (data), MIT (code), per the files in the zip |

Fetching, restoring, and verifying the dump are designed in the staging layer spec (scope feature 4).

## Pinned versions

Image tags and package versions confirmed on 2026-10-25. Python packages are locked exactly in `uv.lock`.

| Component | Pinned | Notes |
|---|---|---|
| Python | 3.12 (`>=3.12,<3.13`) | Installed by uv |
| MongoDB image | `mongo:7.0.45` | Matches the dump's server major (7.0). Ships `mongosh` and `mongorestore`; multi arch (arm64 included). Healthcheck: `mongosh` ping |
| Postgres image | `postgres:18.6` | Data volume mounts at `/var/lib/postgresql` (the 18.x image layout) |
| Neo4j image | `neo4j:2026.10.1-community` | No plugins. Healthcheck: `cypher-shell -d system 'SHOW DATABASE system'` |
| dbt-core | 1.12.6 | |
| dbt-postgres | 1.11.0 | With dbt-adapters 1.24.5 |
| dlt | 1.31.0 | `dlt[postgres]` |
| pydantic-ai-slim | 2.61.0 | `[anthropic]` extra; anthropic SDK 2.3.0 |
| typer | 0.27.3 | click 8.5.0 |
| pydantic-settings | 2.16.1 | pydantic 2.14.1 |
| psycopg | 3.3.6 | `[binary]` |
| neo4j (driver) | 6.4.1 | |
| pymongo | 4.19.0 | Supports MongoDB 7.0 |
| pytest | 9.1.1 | dev |
| testcontainers | 4.15.0 | dev. Modules live at `testcontainers.community.{postgres,neo4j,mongodb}` |

dbt-core, dlt, Typer, and Pydantic AI 2.x resolve together in one environment (protobuf 6.33.6, click 8.5.0), so dbt runs from the project environment, not a separate tool environment.

## Tests

```sh
uv run pytest                    # everything
uv run pytest -m "not integration"   # fast tests only, no Docker
```

The integration tests start their own small Postgres and Neo4j containers. **Stop the compose stack first** (`docker compose --profile all down`) so there is enough Docker memory. They prove the safety rules: the Postgres reader can read `model` (after `dbt run --full-refresh`, twice) and nothing else, and cannot write; Neo4j rejects a `CREATE` while read only.

On Docker Desktop the tests set `TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`, because the per user socket under `~/.docker/run` cannot be mounted into the cleanup container. Set the variable yourself to override it.

## Layout

```
src/orgos/
  config/      typed Settings from env and .env; every connection string is built here
  cli/         the `orgos` command (commands come with the CLI shell spec)
  ingest/      dlt: Mongo to Postgres `raw`
  sqlstore/    Postgres bootstrap and safe query access
  graphstore/  Neo4j load from `model_int` and safe query access
  agent/       the one query agent
  eval/        the eval runner
dbt/           dbt project `orgos` (schemas stg, model_int, model)
scripts/       shell scripts (data fetch)
spikes/        exploratory spikes; their committed results are the deliverable
tests/         pytest; fixtures under tests/fixtures/
results/       eval run records, committed as evidence
```
