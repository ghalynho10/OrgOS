"""The shared preflight's pure judges and its order, with no store (spec 0011 AC-1 to AC-7).

The readers are driven on real containers in `tests/integration/test_eval_stores.py`.
"""

import json
import re
import secrets
from dataclasses import replace
from pathlib import Path

import pytest

from orgos.config import Settings, load_settings
from orgos.eval import EvalError
from orgos.eval.frozen import ANSWERS, FROZEN, QUESTIONS, frozen_line
from orgos.eval.preflight import (
    FINGERPRINT,
    FROZEN_SET,
    RUN_RECORDS,
    SQL_STATE,
    Fingerprint,
    fingerprint_problems,
    frozen_set_problems,
    parse_fingerprint,
    preflight,
    write_fingerprint,
)
from orgos.graphstore.record import RUN_RECORD as GRAPH_RUN_RECORD
from orgos.sqlstore.checks import (
    FOREIGN_KEYS,
    TABLES,
    SqlState,
    index_shape,
    sql_state_problems,
)
from orgos.sqlstore.record import RUN_RECORD as SQL_RUN_RECORD

ROOT = Path(__file__).resolve().parents[1]
LOAD_ID = "1762000000.123456"


def good_sql_state() -> SqlState:
    return SqlState(
        relations=tuple((table, "r") for table in sorted(TABLES)),
        load_ids={table: (LOAD_ID,) for table in TABLES},
        foreign_keys=FOREIGN_KEYS,
        indexes=("CREATE UNIQUE INDEX ON model.issue USING btree (issue_key)",),
        timezone="UTC",
    )


def test_a_good_sql_state_has_no_problems() -> None:
    assert sql_state_problems(good_sql_state(), LOAD_ID) == ()


@pytest.mark.parametrize(
    ("relations", "expected"),
    [
        (
            (("issue__dbt_tmp", "r"),),
            "model.issue__dbt_tmp is left over from a dbt build that stopped partway",
        ),
        ((("plain_probe", "r"),), "model.plain_probe (relkind r) is not one of the 7 tables"),
        ((("issue_view", "v"),), "model.issue_view (relkind v) is not one of the 7 tables"),
    ],
)
def test_an_extra_relation_is_refused(
    relations: tuple[tuple[str, str], ...], expected: str
) -> None:
    state = good_sql_state()
    state = replace(state, relations=state.relations + relations)
    assert sql_state_problems(state, LOAD_ID) == (expected,)


def test_a_missing_table_is_refused() -> None:
    state = good_sql_state()
    state = replace(
        state,
        relations=tuple(item for item in state.relations if item[0] != "person"),
        load_ids={table: ids for table, ids in state.load_ids.items() if table != "person"},
    )
    assert sql_state_problems(state, LOAD_ID) == ("model.person is missing",)


@pytest.mark.parametrize(
    ("held", "shown"),
    [
        ((LOAD_ID, "1761000000.1"), f"1761000000.1, {LOAD_ID}"),
        (("1761000000.1",), "1761000000.1"),
        ((None,), "null"),
        ((), "none (no rows)"),
    ],
)
def test_a_table_not_holding_only_the_record_load_id_is_refused(
    held: tuple[str | None, ...], shown: str
) -> None:
    state = good_sql_state()
    state = replace(state, load_ids={**state.load_ids, "issue": held})
    assert sql_state_problems(state, LOAD_ID) == (
        f"model.issue holds load_id {shown}, expected only {LOAD_ID}",
    )


def test_a_missing_and_an_extra_foreign_key_are_refused() -> None:
    state = good_sql_state()
    extra = ("issue", "parent_key", "issue", "issue_key")
    state = replace(state, foreign_keys=(*FOREIGN_KEYS[1:], extra))
    assert sql_state_problems(state, LOAD_ID) == (
        "foreign key missing: issue.project_key to project.project_key",
        "foreign key not expected: issue.parent_key to issue.issue_key",
    )


def test_a_session_zone_other_than_utc_is_refused() -> None:
    state = replace(good_sql_state(), timezone="America/St_Johns")
    assert sql_state_problems(state, LOAD_ID) == (
        "the reader session zone is 'America/St_Johns', not UTC",
    )


def test_the_spec_lists_seven_tables_and_ten_foreign_keys() -> None:
    assert len(TABLES) == len(set(TABLES)) == 7
    assert len(FOREIGN_KEYS) == len(set(FOREIGN_KEYS)) == 10


@pytest.mark.parametrize(
    ("definition", "shape"),
    [
        (
            "CREATE UNIQUE INDEX issue__dbt_tmp_pkey ON model.issue USING btree (issue_key)",
            "CREATE UNIQUE INDEX ON model.issue USING btree (issue_key)",
        ),
        (
            "CREATE INDEX 3f2a9c ON model.logged_time USING btree (person_key)",
            "CREATE INDEX ON model.logged_time USING btree (person_key)",
        ),
    ],
)
def test_index_definitions_lose_their_generated_names(definition: str, shape: str) -> None:
    assert index_shape(definition) == shape


FINGERPRINT_ITEMS = Fingerprint(
    sql_indexes=("CREATE INDEX ON model.issue USING btree (project_key)",),
    graph_constraints=("NODE_PROPERTY_UNIQUENESS on NODE Issue(key)",),
    graph_indexes=("LOOKUP on NODE", "RANGE on NODE Issue(key) owned by a constraint"),
)


def test_an_unchanged_fingerprint_has_no_problems() -> None:
    assert fingerprint_problems(FINGERPRINT_ITEMS, FINGERPRINT_ITEMS) == ()


def test_an_added_or_removed_index_or_constraint_is_named() -> None:
    live = replace(
        FINGERPRINT_ITEMS,
        sql_indexes=(
            *FINGERPRINT_ITEMS.sql_indexes,
            "CREATE INDEX ON model.issue USING btree (status_name)",
        ),
        graph_constraints=(),
    )
    assert fingerprint_problems(FINGERPRINT_ITEMS, live) == (
        "SQL index added: CREATE INDEX ON model.issue USING btree (status_name)",
        "graph constraint removed: NODE_PROPERTY_UNIQUENESS on NODE Issue(key)",
    )


def test_the_fingerprint_is_written_once_and_never_rewritten(tmp_path: Path) -> None:
    write_fingerprint(tmp_path, FINGERPRINT_ITEMS)
    path = tmp_path / FINGERPRINT
    written = path.read_text(encoding="utf-8")
    assert parse_fingerprint(written) == FINGERPRINT_ITEMS
    with pytest.raises(EvalError, match="already exists and is never rewritten"):
        write_fingerprint(tmp_path, replace(FINGERPRINT_ITEMS, graph_indexes=()))
    assert path.read_text(encoding="utf-8") == written


def test_a_malformed_fingerprint_is_refused() -> None:
    with pytest.raises(EvalError, match="schema fingerprint check"):
        parse_fingerprint(json.dumps({"sql_indexes": []}))


def frozen_root(root: Path, *, list_answers: bool = True) -> Path:
    (root / "eval").mkdir()
    (root / QUESTIONS).write_text("[questions]\n")
    (root / ANSWERS).write_text('{"answers": "placeholder"}\n')
    pinned = (QUESTIONS, ANSWERS) if list_answers else (QUESTIONS,)
    (root / FROZEN).write_text("".join(frozen_line(root, path) + "\n" for path in pinned))
    return root


def test_the_repo_frozen_set_passes() -> None:
    assert frozen_set_problems(ROOT) == ()


def test_a_frozen_file_that_does_not_list_the_answers_is_refused(tmp_path: Path) -> None:
    root = frozen_root(tmp_path, list_answers=False)
    assert frozen_set_problems(root) == (f"{FROZEN} does not list {ANSWERS}",)


def test_no_frozen_file_is_refused(tmp_path: Path) -> None:
    assert frozen_set_problems(tmp_path) == (f"{FROZEN} does not list {ANSWERS}",)


def settings_without_stores() -> Settings:
    # Nothing listens on port 1, so a store read fails at once.
    return load_settings(
        _env_file=None,
        ORGOS_PG_PORT=1,
        ORGOS_NEO4J_BOLT_PORT=1,
        POSTGRES_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_OWNER_PASSWORD=secrets.token_hex(8),
        ORGOS_PG_READER_PASSWORD=secrets.token_hex(8),
        MONGO_INITDB_ROOT_PASSWORD=secrets.token_hex(8),
        ORGOS_MONGO_READER_PASSWORD=secrets.token_hex(8),
        NEO4J_AUTH=f"neo4j/{secrets.token_hex(8)}",
    )


def write_records(root: Path, sql: dict[str, object], graph: dict[str, object]) -> None:
    for path, record in ((SQL_RUN_RECORD, sql), (GRAPH_RUN_RECORD, graph)):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(json.dumps(record))


def test_the_frozen_set_is_checked_first(tmp_path: Path) -> None:
    root = frozen_root(tmp_path, list_answers=False)
    with pytest.raises(EvalError, match=f"refused at the {FROZEN_SET} check"):
        preflight(settings_without_stores(), root)


def test_missing_run_records_are_refused_second(tmp_path: Path) -> None:
    root = frozen_root(tmp_path)
    with pytest.raises(EvalError, match=f"refused at the {RUN_RECORDS} check"):
        preflight(settings_without_stores(), root)


def test_records_with_two_load_ids_are_refused(tmp_path: Path) -> None:
    root = frozen_root(tmp_path)
    write_records(
        root,
        {"status": "ok", "dlt_load_id": LOAD_ID},
        {"status": "ok", "dlt_load_id": "1761000000.1"},
    )
    with pytest.raises(EvalError, match=f"refused at the {RUN_RECORDS} check") as excinfo:
        preflight(settings_without_stores(), root)
    assert "holds dlt_load_id 1761000000.1" in str(excinfo.value)


def test_a_failed_record_is_refused(tmp_path: Path) -> None:
    root = frozen_root(tmp_path)
    write_records(
        root,
        {"status": "ok", "dlt_load_id": LOAD_ID},
        {"status": "fail", "dlt_load_id": LOAD_ID, "failed_step": "compare"},
    )
    with pytest.raises(EvalError, match="says status 'fail'"):
        preflight(settings_without_stores(), root)


def test_good_records_go_on_to_the_sql_state_without_naming_the_login(tmp_path: Path) -> None:
    root = frozen_root(tmp_path)
    write_records(
        root,
        {"status": "ok", "dlt_load_id": LOAD_ID},
        {"status": "ok", "dlt_load_id": LOAD_ID},
    )
    settings = settings_without_stores()
    with pytest.raises(EvalError, match=f"refused at the {SQL_STATE} check") as excinfo:
        preflight(settings, root)
    message = str(excinfo.value)
    assert settings.pg_reader_user not in message
    assert settings.pg_reader_password.get_secret_value() not in message


# Spec 0011 AC-7: the eval code and the two executors never reach for an
# owner, superuser or root login, and never call the 20 table consistency check.
NO_OWNER_LOGIN = re.compile(
    r"pg_owner_\w*|pg_superuser\w*|\bmongo_user\b|\bmongo_password\b|\bmongo_uri\b"
    r"|MONGO_INITDB_ROOT\w*|root_client|pg_conninfo\(\s*[\"'](owner|superuser)[\"']"
    r"|load_consistency"
)
SEARCHED = (
    *sorted((ROOT / "src/orgos/eval").rglob("*.py")),
    ROOT / "src/orgos/sqlstore/query.py",
    ROOT / "src/orgos/graphstore/query.py",
)


def test_the_eval_code_and_the_executors_use_no_owner_or_root_login() -> None:
    found = [
        f"{path.relative_to(ROOT)}:{number}: {match.group(0)}"
        for path in SEARCHED
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if (match := NO_OWNER_LOGIN.search(line))
    ]
    assert found == []


def test_the_search_finds_an_owner_login() -> None:
    assert NO_OWNER_LOGIN.search('conn = psycopg.connect(settings.pg_conninfo("owner"))')
    assert NO_OWNER_LOGIN.search("with root_client(settings) as client:")
    assert NO_OWNER_LOGIN.search("settings.pg_superuser_password")
