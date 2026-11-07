# dbt

## Overview

The dbt project that turns `raw.jira_issue` (written by dlt) into three Postgres schemas: `stg` views, the 12 `model_int` tables both stores are built from, and the 7 served `model` tables the agent's reader login sees. `orgos load sql` runs `dbt build` on the whole project; specs 0006 (`model_int`), 0007 (`model`) and 0009 (the load) are the contracts.

## Tables

| Schema | Model file | Table |
| --- | --- | --- |
| `model_int` | `int_issue`, `int_project`, `int_person`, `int_fix_version` | `issue`, `project`, `person`, `fix_version` |
| `model_int` | `int_in_project`, `int_assigned_to`, `int_reported_by`, `int_created_by`, `int_has_parent` | `in_project`, `assigned_to`, `reported_by`, `created_by`, `has_parent` (single value edges) |
| `model_int` | `int_issue_link`, `int_fixed_in`, `int_logged_time` | `issue_link`, `fixed_in`, `logged_time` |
| `model` | `issue`, `project`, `person`, `fix_version`, `fixed_in`, `issue_link`, `logged_time` | same names |

`model.issue` folds the five single value edge tables into columns; every other `model` table reads one `model_int` table. `model_int` models are named `int_<table>` and set `alias: <table>` in `model_int.yml`, because `model` has tables of the same names.

## Key files

| File | Owns |
| --- | --- |
| `dbt_project.yml` | Schemas, materializations, enforced contracts, the `ANALYZE` post hook and reader grant on `model`, `store_failures` into `test_failures` |
| `macros/generate_schema_name.sql` | Schema names exactly `stg`, `model_int`, `model` (no target prefix) |
| `models/stg/sources.yml` | The `raw.jira_issue` source (`mongo_id`, `_dlt_load_id`, `fields`, `changelog`) |
| `models/model_int/model_int.yml` | Every column and `data_type` of the 12 tables; spec 0008's graph mapping tests read this file |
| `models/model/model.yml` | The 7 table contracts, keys, 10 indexes, and the `matches_model_int` compare tests |
| `models/model_int/unit_tests.yml` | dbt unit tests for rules a data test cannot pin (amounts, UTC conversion, lowest `_id`) |
| `macros/tests/` | Generic tests: `matches_model_int`, `no_empty_strings`, `edge_type_equals` |
| `tests/` | Singular tests: stg validity, table lists, keys, indexes, `ANALYZE`, session zone |

## Conventions

- Every `model_int` and `model` table carries `source_doc_id` and `load_id` as its last two columns, and holds one `load_id` after a build (spec 0009 AC-17).
- The `model_int` edge tables share the `&edge_columns` YAML anchor in `model_int.yml` (`edge_id`, `src_id`, `dst_id`, `edge_type`, `source_doc_id`, `load_id`).
- `model_int` and `model` columns are declared in YAML with the contract enforced; a new column needs its YAML entry or the build fails.
- A failing test's stored rows are printed by `orgos load sql` (up to 20), so a singular test selects the key (link ID, issue key, history ID) the reader needs to find the bad row.
- No `dbt_utils` and no `packages.yml`; write a macro under `macros/tests/` instead.
- Singular tests that read the catalog (`information_schema`, `pg_class`) declare `-- depends_on: {{ ref(...) }}` lines so they run after the models they check.

## Gotchas

- The table lists live in four places: `tests/model_int_has_the_12_tables.sql`, `tests/model_has_the_7_tables.sql`, `EXPECTED_TABLES` in `src/orgos/sqlstore/figures.py`, and the YAML. Adding or renaming a table means changing all of them.
- Build the `model` tables together. Rebuilding one alone (for example `dbt run --select person`) replaces it and drops the foreign keys that point at it, and the `model_keys` test then fails.
- Every build must run with `PGTZ=America/St_Johns` (the root command adds it); `tests/build_session_timezone.sql` fails otherwise and skips everything built on `stg_issue_doc`.
- The three `scaffold_probe_*` models are disabled unless `--vars '{orgos_scaffold_probes: true}'` is passed; only `tests/integration/test_sql_safety.py` uses them.
- `target/run_results.json` is deleted before each `orgos load sql` dbt run; its absence afterwards means dbt stopped early.

## Related specs

- [0006 entity model](../docs/specs/0006-entity-model/index.md): the 12 `model_int` tables
- [0007 SQL schema](../docs/specs/0007-sql-schema/index.md): the 7 `model` tables
- [0009 SQL load](../docs/specs/0009-sql-load/index.md): the load, tests and run record

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
