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
uv run orgos eval describe                           # rewrite eval/data-description.md, append to eval/exploration.log; writes nothing to Mongo
uv run orgos eval check-questions [--file <path>]    # check the questions and amendments against every checker rule; reads no database
uv run orgos eval answers [--check] [--only <id>]    # known answers from raw Mongo; a full run refuses once frozen; --check and --only write nothing
uv run orgos load sql                                # Mongo to raw, dbt build with tests, load consistency, figure gate; writes results/sql_load/run_record.json pass or fail
uv run orgos load graph                              # model_int into Neo4j: writable, wipe, load, read only again, every check and the compare; writes results/graph_load/run_record.json pass or fail
uv run orgos eval preflight                          # frozen set, run records, SQL state, graph state, schema fingerprint, in that order; writes nothing
uv run orgos eval gold try Qnn --store sql|graph [--hypothesis T --cites C]   # one try of a gold pair, appended to results/gold/tries.jsonl; from try 2 of a round both flags are required
uv run orgos eval gold decide Qnn --store S (--rewrite | --discrepancy TEXT)  # after 3 failed tries in a round: open a new round or record a known answer discrepancy
uv run orgos eval gold accept Qnn --store S --reviewed                        # pin a correct pair's files in gold/ACCEPTED; reads no store
uv run orgos eval version add --agent module:factory [--reason R --exposed-by FILE ... --kind bug_fix|behaviour_change]   # append to results/agent_runs/versions.jsonl, 3 versions at most; --exposed-by once per run file of the previous version; calls no model
uv run orgos eval run --agent replay|module:factory [--estimate | --go --max-cost-usd X] [--only Qnn] [--store S] [--max-run K]   # replay is free; a real agent needs its version logged, PLAN.json from --estimate, --go and a cap
uv run orgos ask "<question>" --store sql|graph [--go]   # one plain question, not scored, writes no file; preflight, then the bound for one run; paid model calls only with --go
PGTZ=America/St_Johns uv run --env-file .env dbt <cmd> --project-dir dbt --profiles-dir dbt   # the build session zone (spec 0009 AC-14)
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
- The eval set is frozen at the tag `eval-frozen` (spec 0005): `eval/FROZEN` holds the hashes of `eval/questions.toml`, `eval/amendments.toml` and `eval/answers.json`, `tests/test_eval_frozen.py` fails on any change to them in pre-commit and CI, and `orgos eval answers` refuses to write. `orgos.eval.compare` is the one comparison rule and is never changed after results.
- The eval runner (spec 0011, amended by spec 0012) lives in `orgos.eval`: `preflight` (the shared five checks, run first by every command that queries a store, also by feature 13), `gold` (pairs, try, decide, accept, the gold summary), `gold_adapters` (one pure adapter per question), `tries`, `literal`, `score`, `runs` (the plan, the cost gate, run files, the versions log), `replay` and `handoff` (the only types the runner and the agent exchange; `orgos.eval` never imports `orgos.agent`).
- Every eval store query goes through `orgos.sqlstore.query.run_sql` or `orgos.graphstore.query.run_cypher`: read only, the one `ORGOS_QUERY_TIMEOUT_S`, and `STORE_ROW_CAP` (200,000 rows, in `orgos.config`), a whole result or a named error, never a partial one.
- Gold queries live in `gold/sql/` and `gold/cypher/` (answer, one sources file per step, stage files for a zero guarded question, explain), reviewed by hand against `gold/CHECKLIST.md`. `gold/ACCEPTED` pins accepted pairs by sha256 and is written only by `gold accept`; `results/gold/tries.jsonl` is append only and never hand edited, and the gold headline counts a pair only when its pins hold and the log holds its accept event. `results/gold/schema_fingerprint.json` is written once, by the first `gold try`.
- A `path` gold pair has no sources file: its answer file returns one row per path, a `path` column with the IDs in order and one list column per kind, named after the kind, with one item per hop (the agent's spec 0012 AC-19 convention). `path_rows()` in `orgos.eval.gold_adapters` reshapes it (spec 0011 AC-11 to AC-13, amended 2026-11-12).
- `results/agent_runs/<version>/` holds `PLAN.json` and one run file per question, store and run number, each written once; only `summary.json` is rewritten. A correct run file holds known answer values, so gold drafting never opens `results/agent_runs/`.
- The query agent (spec 0012) lives in `orgos.agent`, loaded as `orgos.agent:build_agent`, which checks both store sessions before any other read from the stores (spec 0012 AC-11; the question and layout files are read first). Provider code (model, thinking, caching, client retries, prices) lives only in `models`; `prompt`, `describe_sql`, `describe_graph` and the one shared `meanings.toml` give both stores the same instructions and descriptions; `tools`, `answer` and `outcome` are pure. `src/orgos/agent/prices.toml` is filled by hand from the provider page with its date, and the build refuses a model with no entry. Any change to these texts, tools or settings changes the version hash, so it is a new agent version (spec 0011 AC-32).
- `src/orgos/agent/layouts.toml` holds each scored question's evidence layout (spec 0015), written from the known answers and the engineer's rulings, never from a run file. The one entry gives both the block's last `Sources layout` line and the soft layout check, and it is hashed in the `question_blocks` part. `agent.py` is not in the version hash, so any decision logic goes in `answer.py`, which is.
- Config is read only through `orgos.config.Settings` and validated at startup; errors surface as one `ConfigError` message. No `os.environ` reads elsewhere.
- Every module has a docstring saying why it exists; every public function has one.
- Errors: each subpackage raises its own named exception; the CLI turns them into a clear message and a non zero exit. No bare `except`.
- CLI: each command loads `Settings` itself, so help needs no `.env`. `run()` in `src/orgos/cli/__init__.py` is the only place that prints errors and picks exit codes; a new named error gets one row in `EXIT_CODES` (spec 0002).
- Conventional commits: `feat(scope): …`, `fix: …`, `chore: …`.

## Tooling and tests

- Lint + format: Ruff (`ruff check`, `ruff format`), configured in `pyproject.toml`. Typecheck: mypy as above. Installed by `/develop tooling`.
- Unit tests cover pure logic with no stores. dbt data tests are built with the models and run on every `dbt build`. Integration tests run on real stores through Testcontainers and `tests/fixtures/` (loads, reruns, safety rules, reconciliation counts). No database mocks.
- `tests/fixtures/corpus/` holds 270 corpus issues as MongoDB canonical extended JSON, with prose replaced by a placeholder and every field kept, one file per project, with `manifest.json` and a README naming what was replaced (spec 0004). It and `eval/answers.json` are the two paths exempt from the 1 MB large file hook in `.pre-commit-config.yaml`.
- The agent is tested with Pydantic AI test models and never calls a real model; real calls happen only in eval runs. Tests check the code works, the eval measures graph vs SQL, and eval scores never fail CI.
- Before commit (pre-commit hooks): lint, format, typecheck, `dbt parse` (no database needed), `uv run pytest -m "not integration"`.
- CI (GitHub Actions, on push): lint, typecheck, all of pytest including integration.

## Git

- integration: on
- branch prefix: feat/
- commit: per-milestone
- `main` is protected: the `no-commit-to-branch` hook refuses a commit on `main`, so work lands through a PR.
- No `Co-Authored-By` trailer: a `commit-msg` hook rejects any commit that carries one.

## Agent skills

Stack skills live in `.agents/skills/` (installed locally, not tracked in git) and are listed per tool in spec 0001 (`dbt-labs/dbt-agent-skills`, `dlt-hub/dlthub-ai-harness`, `pydantic/skills`, `neo4j-contrib/neo4j-skills`, `mongodb/agent-skills`). Open only the ones a task needs.
Declined: a wider skills and MCP search (2026-10-25, the installed set covers the stack).

## Circuit breaker

If the same problem persists after one corrective prompt, stop and run /recover before trying again. It diagnoses an isolated bug (routes to /debug), a session gone wrong through repeated patching (hard reset), or a foundation resting on a wrong assumption (rethink). It pauses for confirmation before a hard reset or a rethink; a reset note goes to `docs/session-notes.md`, which /checkpoint reads and ages out.

## Standing rules

Read [docs/reflexes.md](docs/reflexes.md) before making changes: standing rules for how work is done here, one line each, written by /reflex. A rule that has become a plain convention belongs in this file instead; /reflex flags it and the engineer moves it.

## Context files

- [dbt/AGENTS.md](dbt/AGENTS.md): the dbt project, the 12 `model_int` and 7 `model` table names, and its build gotchas
- [src/orgos/graphstore/AGENTS.md](src/orgos/graphstore/AGENTS.md): the graph load, its labels and relationship types, and its gotchas
<!-- Nested AGENTS.md files are listed here as they are created -->

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
