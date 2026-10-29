# OrgOS

## Stack

- **Language / Runtime**: Python 3.12 (`>=3.12,<3.13`), src layout package `orgos`, Typer CLI
- **Stores**: MongoDB (untouched staging), Postgres (SQL store), Neo4j Community (graph store), all in one Docker Compose file
- **Key dependencies**: dlt then dbt (one entity layer feeds both stores), neo4j driver, psycopg 3, Pydantic AI 2.x, pydantic-settings
- **Package manager**: uv, `uv.lock` committed
- Source of truth: spec 0001 (docs/specs/0001-stack-and-architecture/index.md)

## Build approach

Tracer Bullet: one question working end to end through both stores and the agent first, then each slice widens that working thread.

## Commands

```bash
uv sync                                              # install
uv run pre-commit install                            # pre-commit and commit-msg hooks, once per clone
docker compose --profile all up -d --wait            # all three stores, healthy
uv run orgos bootstrap                               # Postgres roles, schemas, grants (rerunnable)
uv run orgos mongo restore [--check] [--reset]       # restore the Jira dump into Mongo; --check writes nothing
uv run orgos mongo verify                            # live Mongo counts against the restore record
uv run --env-file .env dbt <cmd> --project-dir dbt --profiles-dir dbt
uv run pytest -m "not integration"                   # fast tests, no Docker
uv run pytest                                        # all; stop the compose stack first
```

## Specs

Stored in `docs/specs/`. Format: always the directory shape, `docs/specs/NNNN-title/` with `index.md` and `rationale.md` (plus `verify.md` when there are verify steps). Never a single file spec, even for a small decision.

## Rules

- Functional core, imperative shell. Pure functions for logic; database and model calls only at the edges. One deliberate exception from spec 0001: ports and adapters around the LLM provider (orgos.agent.models), so swapping providers stays a config change.
- Data passed between steps is immutable: frozen dataclasses or frozen pydantic models, tuples over lists. Module level names are constants only; no shared mutable state.
- Plain functions over classes where a function works; compose rather than inherit. `X | None` is fine; never return a sentinel.
- Strict typing: `mypy --strict` (pydantic plugin) on `src/orgos`. Overrides only per untyped third party module, never a blanket ignore.
- Folders match the scaffold: `config`, `cli`, `mongostore`, `ingest`, `sqlstore`, `graphstore`, `agent`, `eval` under `src/orgos/`; new code goes in the subpackage for its stage or store.
- Spikes live in `spikes/NNNN-<name>/`: throwaway scripts outside ruff and mypy, run with `uv run --env-file .env python spikes/<dir>/<script>.py`. Their committed `results/` and README are the deliverable, never the Mongo inputs.
- The corpus is the 7 Jira collection projects in `spikes/0001-time-data-coverage/results/corpus.json` (spec 0004). Hours come from time entries rebuilt from the changelog, never from issue level `timespent`, and the SQL load, the graph load and the known answers drop the same flagged bot accounts (spec 0004, AC-8 and AC-10).
- Config is read only through `orgos.config.Settings` and validated at startup; errors surface as one `ConfigError` message. No `os.environ` reads elsewhere.
- Every module has a docstring saying why it exists; every public function has one.
- Errors: each subpackage raises its own named exception; the CLI turns them into a clear message and a non zero exit. No bare `except`.
- CLI: each command loads `Settings` itself, so help needs no `.env`. `run()` in `src/orgos/cli/__init__.py` is the only place that prints errors and picks exit codes; a new named error gets one row in `EXIT_CODES` (spec 0002).
- Conventional commits: `feat(scope): …`, `fix: …`, `chore: …`.

## Tooling and tests

- Lint + format: Ruff (`ruff check`, `ruff format`), configured in `pyproject.toml`. Typecheck: mypy as above. Installed by `/develop tooling`.
- Unit tests cover pure logic with no stores. dbt data tests are built with the models and run on every `dbt build`. Integration tests run on real stores through Testcontainers and `tests/fixtures/` (loads, reruns, safety rules, reconciliation counts). No database mocks.
- `tests/fixtures/corpus/` holds 270 prose stripped corpus issues as MongoDB canonical extended JSON, one file per project, with `manifest.json` and a README naming what was replaced (spec 0004). It is the one path exempt from the 1 MB large file hook in `.pre-commit-config.yaml`.
- The agent is tested with Pydantic AI test models and never calls a real model; real calls happen only in eval runs. Tests check the code works, the eval measures graph vs SQL, and eval scores never fail CI.
- Before commit (pre-commit hooks): lint, format, typecheck, `dbt parse` (no database needed), `uv run pytest -m "not integration"`.
- CI (GitHub Actions, on push): lint, typecheck, all of pytest including integration.

## Git

- integration: on
- branch prefix: feat/
- commit: per-milestone

## Agent skills

Stack skills live in `.agents/skills/` (installed locally, not tracked in git) and are listed per tool in spec 0001 (`dbt-labs/dbt-agent-skills`, `dlt-hub/dlthub-ai-harness`, `pydantic/skills`, `neo4j-contrib/neo4j-skills`, `mongodb/agent-skills`). Open only the ones a task needs.
Declined: a wider skills and MCP search (2026-10-25, the installed set covers the stack).

## Circuit breaker

If the same problem persists after one corrective prompt, stop and run /recover before trying again. It diagnoses an isolated bug (routes to /debug), a session gone wrong through repeated patching (hard reset), or a foundation resting on a wrong assumption (rethink). It pauses for confirmation before a hard reset or a rethink; a reset note goes to `docs/session-notes.md`, which /checkpoint reads and ages out.

## Context files

<!-- Nested AGENTS.md files are listed here as they are created -->

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
