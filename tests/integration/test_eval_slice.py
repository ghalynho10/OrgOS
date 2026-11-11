"""Slice 1 end to end on real containers (spec 0011 AC-1 to AC-6, AC-13, AC-14, AC-19, AC-30,
AC-34, AC-36, AC-37).

A temporary repo holds a made up Q01 over two projects, its known answer worked
out by hand from the rows below, the two load run records and the gold pair in
both stores. Postgres gets its own database and roles with the 7 `model` tables
and the 10 foreign keys; Neo4j is written with the mapping's constraints, then
restarted read only on the same volume. The real `eval/answers.json` is never read.

Rows: AAA-1 qualifies (Q1 2013, unresolved, no assignee); AAA-2 has an assignee;
AAA-3 is created at the excluded end bound; BBB-1 is resolved. So the known
answer is AAA 1, BBB 0, with the sources step `issue: [AAA-1]`.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import docker
import psycopg
import pytest
from pydantic import SecretStr
from testcontainers.community.neo4j import Neo4jContainer

from orgos.config import Settings
from orgos.eval import EvalError
from orgos.eval.frozen import ANSWERS, FROZEN, QUESTIONS, frozen_line
from orgos.eval.gold import ACCEPTED, gold_accept, gold_summary, gold_try, pin_problems
from orgos.eval.preflight import FINGERPRINT, preflight
from orgos.eval.runs import (
    estimate,
    read_run_files,
    run_agent,
    run_path,
    version_add,
    write_once,
)
from orgos.eval.tries import read_tries
from orgos.graphstore.mapping import load_mapping
from orgos.graphstore.statements import create_constraint
from orgos.sqlstore.bootstrap import bootstrap
from tests.integration.conftest import NEO4J_PASSWORD, _neo4j

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
LOAD_ID = "1762000000.654321"
READING = (
    "Population: issues whose current project key is AAA or BBB. Count qualifying issues"
    " per project and list both projects, including zeros.\n"
    "Sources, step by step: (1) select issues by project key; (2) retain those created in"
    " [2013-01-01T00:00:00Z, 2013-04-01T00:00:00Z); (3) retain the unresolved; (4) retain"
    " those with no assignee; (5) count per project."
)
QUESTION = f'''
[[question]]
id = "Q01"
text = "Per project, how many Q1 2013 (UTC) issues are unresolved and unassigned?"
type = "aggregate"
hops = 0
hop_mode = "fixed"
shape = "map"
unit = "count"
fields = ["fields.project.key"]
reading = """
{READING}
"""
data = "real"
tracer = true
'''
KNOWN = {
    "shape": "map",
    "value": [
        {"id": "project:AAA", "value": {"int": 1}},
        {"id": "project:BBB", "value": {"int": 0}},
    ],
    "sources": [{"issue": ["AAA-1"]}],
}
CONDITIONS_SQL = """
    AND i.created_at >= TIMESTAMPTZ '2013-01-01 00:00:00+00'
    AND i.created_at < TIMESTAMPTZ '2013-04-01 00:00:00+00'
    AND (i.resolution_name IS NULL OR i.resolution_name = '')
    AND (i.assignee_key IS NULL OR i.assignee_key = '')"""
CONDITIONS_CYPHER = """
    i.createdAt >= datetime('2013-01-01T00:00:00Z')
    AND i.createdAt < datetime('2013-04-01T00:00:00Z')
    AND (i.resolutionName IS NULL OR i.resolutionName = '')
    AND NOT EXISTS { (i)-[:ASSIGNED_TO]->(:Person) }"""
GOLD = {
    "gold/sql/Q01.answer.sql": f"""SELECT p.project_key, count(DISTINCT i.issue_key) AS issues
FROM model.project AS p
LEFT JOIN model.issue AS i ON i.project_key = p.project_key{CONDITIONS_SQL}
WHERE p.project_key IN ('AAA', 'BBB')
GROUP BY p.project_key
""",
    "gold/sql/Q01.sources.1.sql": f"""SELECT DISTINCT i.issue_key AS issue
FROM model.issue AS i
WHERE i.project_key IN ('AAA', 'BBB'){CONDITIONS_SQL}
""",
    "gold/cypher/Q01.answer.cypher": f"""MATCH (p:Project) WHERE p.projectKey IN ['AAA', 'BBB']
OPTIONAL MATCH (i:Issue)-[:IN_PROJECT]->(p)
WHERE{CONDITIONS_CYPHER}
RETURN p.projectKey AS project_key, count(DISTINCT i) AS issues
""",
    "gold/cypher/Q01.sources.1.cypher": f"""MATCH (i:Issue)-[:IN_PROJECT]->(p:Project)
WHERE p.projectKey IN ['AAA', 'BBB'] AND{CONDITIONS_CYPHER}
RETURN DISTINCT i.issueKey AS issue
""",
    "gold/sql/Q01.explain.md": "step 1, step 2, step 3, step 4 and step 5 in SQL.\n",
    "gold/cypher/Q01.explain.md": "step 1, step 2, step 3, step 4 and step 5 in Cypher.\n",
}
MODEL_DDL = (
    "CREATE TABLE model.project (project_key text PRIMARY KEY, source_doc_id text, load_id text)",
    "CREATE TABLE model.person (person_key text PRIMARY KEY, source_doc_id text, load_id text)",
    "CREATE TABLE model.fix_version (version_id text PRIMARY KEY, source_doc_id text,"
    " load_id text)",
    "CREATE TABLE model.issue (issue_key text PRIMARY KEY, created_at timestamptz,"
    " resolution_name text,"
    " project_key text NOT NULL REFERENCES model.project (project_key),"
    " assignee_key text REFERENCES model.person (person_key),"
    " reporter_key text REFERENCES model.person (person_key),"
    " creator_key text REFERENCES model.person (person_key),"
    " parent_key text, source_doc_id text, load_id text)",
    "CREATE TABLE model.logged_time (history_id text PRIMARY KEY,"
    " person_key text NOT NULL REFERENCES model.person (person_key),"
    " issue_key text NOT NULL REFERENCES model.issue (issue_key),"
    " source_doc_id text, load_id text)",
    "CREATE TABLE model.fixed_in (issue_key text NOT NULL REFERENCES model.issue (issue_key),"
    " version_id text NOT NULL REFERENCES model.fix_version (version_id),"
    " source_doc_id text, load_id text, PRIMARY KEY (issue_key, version_id))",
    "CREATE TABLE model.issue_link (link_id text PRIMARY KEY,"
    " from_issue_key text NOT NULL REFERENCES model.issue (issue_key),"
    " to_issue_key text NOT NULL REFERENCES model.issue (issue_key),"
    " source_doc_id text, load_id text)",
)
ROWS = (
    "INSERT INTO model.project VALUES ('AAA', 'd', %(l)s), ('BBB', 'd', %(l)s)",
    "INSERT INTO model.person VALUES ('u1', 'd', %(l)s)",
    "INSERT INTO model.issue (issue_key, created_at, resolution_name, project_key,"
    " assignee_key, source_doc_id, load_id) VALUES"
    " ('AAA-1', '2013-02-01T10:00:00Z', NULL, 'AAA', NULL, 'd', %(l)s),"
    " ('AAA-2', '2013-02-02T10:00:00Z', NULL, 'AAA', 'u1', 'd', %(l)s),"
    " ('AAA-3', '2013-04-01T00:00:00Z', '', 'AAA', NULL, 'd', %(l)s),"
    " ('BBB-1', '2013-03-01T10:00:00Z', 'Fixed', 'BBB', NULL, 'd', %(l)s)",
    # One row in each other table, so every table holds L (AC-4); none touches Q01.
    "INSERT INTO model.fix_version VALUES ('v1', 'd', %(l)s)",
    "INSERT INTO model.fixed_in VALUES ('AAA-2', 'v1', 'd', %(l)s)",
    "INSERT INTO model.issue_link VALUES ('k1', 'AAA-2', 'BBB-1', 'd', %(l)s)",
    "INSERT INTO model.logged_time VALUES ('h1', 'u1', 'AAA-2', 'd', %(l)s)",
)
GRAPH = """
CREATE (aaa:Project {projectKey: 'AAA', loadId: $l}), (bbb:Project {projectKey: 'BBB', loadId: $l}),
       (u1:Person {personKey: 'u1', loadId: $l}),
       (i1:Issue {issueKey: 'AAA-1', createdAt: datetime('2013-02-01T10:00:00Z'), loadId: $l}),
       (i2:Issue {issueKey: 'AAA-2', createdAt: datetime('2013-02-02T10:00:00Z'), loadId: $l}),
       (i3:Issue {issueKey: 'AAA-3', createdAt: datetime('2013-04-01T00:00:00Z'),
                  resolutionName: '', loadId: $l}),
       (b1:Issue {issueKey: 'BBB-1', createdAt: datetime('2013-03-01T10:00:00Z'),
                  resolutionName: 'Fixed', loadId: $l}),
       (i1)-[:IN_PROJECT {loadId: $l}]->(aaa), (i2)-[:IN_PROJECT {loadId: $l}]->(aaa),
       (i3)-[:IN_PROJECT {loadId: $l}]->(aaa), (b1)-[:IN_PROJECT {loadId: $l}]->(bbb),
       (i2)-[:ASSIGNED_TO {loadId: $l}]->(u1)
"""


def _git(root: Path, *args: str) -> None:
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def _make_root(root: Path) -> Path:
    _git(root, "init", "--quiet")
    _git(
        root,
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "user.name=test",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "empty",
    )
    (root / "eval").mkdir()
    (root / QUESTIONS).write_text(QUESTION)
    (root / ANSWERS).write_text(
        json.dumps({"amendments_sha256": None, "answers": {"Q01": KNOWN}, "questions_sha256": "x"})
    )
    (root / FROZEN).write_text(
        "".join(frozen_line(root, path) + "\n" for path in (QUESTIONS, ANSWERS))
    )
    for path in ("results/sql_load/run_record.json", "results/graph_load/run_record.json"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(json.dumps({"status": "ok", "dlt_load_id": LOAD_ID}))
    for path, text in GOLD.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    (root / "src/orgos/eval").mkdir(parents=True)
    shutil.copy(REPO / "src/orgos/eval/compare.py", root / "src/orgos/eval/compare.py")
    return root


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_root(tmp_path_factory.mktemp("slice"))


@pytest.fixture
def fresh_root(tmp_path: Path) -> Path:
    """A repo of its own, so a test's tries and run files touch no other test."""
    return _make_root(tmp_path)


@pytest.fixture(scope="module")
def slice_pg(pg_settings: Settings) -> Settings:
    settings = pg_settings.model_copy(
        update={
            "pg_database": "orgos_slice",
            "pg_owner_user": "slice_owner",
            "pg_owner_password": SecretStr(secrets.token_hex(8)),
            "pg_reader_user": "slice_reader",
            "pg_reader_password": SecretStr(secrets.token_hex(8)),
        }
    )
    bootstrap(settings)
    with psycopg.connect(settings.pg_conninfo("owner"), autocommit=True) as conn:
        for statement in MODEL_DDL:
            conn.execute(statement)
        for statement in ROWS:
            conn.execute(statement, {"l": LOAD_ID})
    return settings


def _wait_online(container: Neo4jContainer) -> None:
    deadline = time.monotonic() + 120
    with container.get_driver() as driver:
        while time.monotonic() < deadline:
            records = driver.execute_query(
                "SHOW DATABASES YIELD name, currentStatus WHERE name = 'neo4j'"
                " RETURN currentStatus",
                database_="system",
            ).records
            if records and records[0]["currentStatus"] == "online":
                return
            time.sleep(1)
    raise AssertionError("neo4j did not come online read only")


@pytest.fixture(scope="module")
def slice_graph() -> Iterator[Neo4jContainer]:
    """Written with the mapping's constraints and the rows, then restarted read only."""
    volume = f"orgos-test-slice-{uuid.uuid4().hex[:8]}"
    try:
        with _neo4j(volume) as writable:
            with writable.get_driver() as driver:
                for node in load_mapping().nodes:
                    driver.execute_query(create_constraint(node), database_="neo4j")
                driver.execute_query(GRAPH, {"l": LOAD_ID}, database_="neo4j")
                driver.execute_query("CALL db.awaitIndexes(60)", database_="neo4j")
            # A clean shutdown: a read only database cannot run recovery on restart.
            writable.get_wrapped_container().stop(timeout=60)
        with _neo4j(volume).with_env("NEO4J_server_databases_read__only", "neo4j") as container:
            _wait_online(container)
            yield container
    finally:
        docker.from_env().volumes.get(volume).remove(force=True)


@pytest.fixture(scope="module")
def settings(slice_pg: Settings, slice_graph: Neo4jContainer) -> Settings:
    return slice_pg.model_copy(
        update={
            "neo4j_host": slice_graph.get_container_host_ip(),
            "neo4j_bolt_port": int(slice_graph.get_exposed_port(7687)),
            "neo4j_auth": SecretStr(f"neo4j/{NEO4J_PASSWORD}"),
        }
    )


def test_slice_one_end_to_end(settings: Settings, root: Path) -> None:
    # The preflight passes on a good state, before any fingerprint exists (AC-1 to AC-6).
    checked = preflight(settings, root)
    assert checked.load_id == LOAD_ID and not checked.fingerprint_saved

    # One try per store is correct, prints no ID, and writes the fingerprint once (AC-6, AC-13).
    for store in ("sql", "graph"):
        lines = gold_try(settings, root, "Q01", store, hypothesis=None, cites=None)
        assert lines[:5] == (
            f"Q01 {store}: round 1, try 1, outcome correct",
            "  answer_match: true",
            "  source_match: true",
            "  correct: true",
            "  step 1: 0 missing, 0 extra",
        )
        assert not any("AAA-1" in line for line in lines)
    assert (root / FINGERPRINT).is_file()
    assert "gold sql: 0/1 correct" in "\n".join(gold_summary(root))
    assert "correct, not reviewed 1" in "\n".join(gold_summary(root))

    # Accept and pin both pairs (AC-19, AC-20).
    for store in ("sql", "graph"):
        gold_accept(root, "Q01", store, reviewed=True)
    assert len((root / ACCEPTED).read_text().splitlines()) == 6
    assert pin_problems(root) == ()
    assert gold_summary(root)[0].startswith("gold graph: 1/1 correct")

    # Replay writes 3 correct run files per store, then nothing on a rerun (AC-34, AC-36).
    lines = run_agent(
        settings, root, "replay", go=False, max_cost_usd=None, only="Q01", store=None, max_run=None
    )
    assert lines[0] == "wrote 6 run files under results/agent_runs/replay/"
    assert "replay sql: 3/3 correct (3/3 without the zero guarded none)" in lines
    files = read_run_files(root, "replay")
    assert [f["outcome"] for f in files] == ["correct"] * 6
    assert {f["load_id"] for f in files} == {LOAD_ID}
    again = run_agent(
        settings, root, "replay", go=False, max_cost_usd=None, only="Q01", store=None, max_run=None
    )
    assert again[0] == "every planned run number already has a file; nothing to run"


def test_an_index_added_after_the_first_try_is_refused(
    settings: Settings, slice_pg: Settings, root: Path
) -> None:
    if not (root / FINGERPRINT).is_file():
        pytest.skip("needs the fingerprint the end to end test writes")
    with psycopg.connect(slice_pg.pg_conninfo("owner"), autocommit=True) as conn:
        conn.execute("CREATE INDEX slice_added ON model.issue (resolution_name)")
        try:
            with pytest.raises(EvalError, match="schema fingerprint check") as excinfo:
                preflight(settings, root)
            assert "SQL index added: CREATE INDEX ON model.issue USING btree (resolution_name)" in (
                str(excinfo.value)
            )
        finally:
            conn.execute("DROP INDEX model.slice_added")
    assert preflight(settings, root).fingerprint_saved


# covers: AC-14, AC-15, AC-17 (the literal refusal is a logged failed try that names no literal)
def test_a_literal_in_a_gold_query_is_a_failed_try_that_names_no_literal(
    settings: Settings, fresh_root: Path
) -> None:
    sources = fresh_root / "gold/sql/Q01.sources.1.sql"
    sources.write_text(sources.read_text() + "-- AAA-1\n")
    line = len(sources.read_text().splitlines())
    with pytest.raises(EvalError, match="this counts as a failed try") as excinfo:
        gold_try(settings, fresh_root, "Q01", "sql", hypothesis=None, cites=None)
    message = str(excinfo.value)
    assert f"gold/sql/Q01.sources.1.sql:{line}: holds a literal from the known answer" in message
    assert "AAA-1" not in message
    (logged,) = read_tries(fresh_root)
    assert (logged["outcome"], logged["correct"], logged["try"]) == ("literal", False, 1)
    assert "AAA-1" not in json.dumps(logged)


STUB = "tests.test_eval_runs:stub_factory"


# covers: AC-30, AC-32, AC-35, AC-37 (the gate is wired into `run`; the stub calls no model)
def test_a_real_agent_is_gated_before_any_run_then_written_once(
    settings: Settings, fresh_root: Path
) -> None:
    limited = settings.model_copy(
        update={"agent_request_limit": 10, "agent_token_limit": 50_000, "agent_tool_call_limit": 8}
    )

    def run(**options: Any) -> tuple[str, ...]:
        chosen = {"go": True, "max_cost_usd": 1.0, "only": "Q01", "store": "sql", "max_run": 1}
        return run_agent(limited, fresh_root, STUB, **(chosen | options))

    with pytest.raises(EvalError, match=r"has no entry in results/agent_runs/versions\.jsonl"):
        run()
    version_add(limited, fresh_root, STUB, reason=None, exposed_by=None, kind=None)
    with pytest.raises(EvalError, match=r"has no PLAN\.json"):
        run()
    estimate(limited, fresh_root, STUB, only="Q01")
    # The plan's timeout must be the run's (review finding); the stub prices are $3 and $15.
    planned = limited.query_timeout_s
    drifted = limited.model_copy(update={"query_timeout_s": planned + 1})
    drift = f"holds a query timeout of {planned} s, the current setting is {planned + 1} s"
    with pytest.raises(EvalError, match=drift):
        run_agent(
            drifted, fresh_root, STUB, go=True, max_cost_usd=1.0, only="Q01", store="sql", max_run=1
        )
    # Before the first --go, the refusal shows the upper bound of one run (review finding).
    with pytest.raises(EvalError, match="only with --go") as excinfo:
        run(go=False)
    assert (
        "upper bound for one run: $0.7500 (the token limit 50,000 times the higher price $15"
        " per million)" in str(excinfo.value)
    )
    for options, reason in (
        ({"go": False}, "only with --go"),
        ({"max_cost_usd": None}, "only with --max-cost-usd"),
        ({"store": None}, "no finished run file yet"),
        ({"max_run": None}, "no finished run file yet"),
    ):
        with pytest.raises(EvalError, match=reason):
            run(**options)
    assert read_run_files(fresh_root, "v1") == ()

    assert "wrote 1 run files under results/agent_runs/v1/" in run()
    (first,) = read_run_files(fresh_root, "v1")
    assert (first["agent_version"], first["load_id"]) == ("v1", LOAD_ID)
    assert first["usage"]["cost_usd"] == 0.01

    # The measured mean, $0.01, times the 5 runs left is above a $0.02 cap.
    with pytest.raises(EvalError, match=r"times 5 runs is \$0\.0500, above the cap \$0\.0200"):
        run(store=None, max_run=None, max_cost_usd=0.02)
    assert len(read_run_files(fresh_root, "v1")) == 1
    lines = run(store=None, max_run=None)
    assert "cost this invocation $0.0500; cumulative for version v1 $0.0600" in lines
    assert len(read_run_files(fresh_root, "v1")) == 6

    # A run file of the version on another load ID refuses the next run (AC-37).
    write_once(
        run_path(fresh_root, "v1", "Q01", "sql", 4),
        dict(first) | {"run": 4, "load_id": "1761000000.1"},
    )
    with pytest.raises(
        EvalError, match=re.escape(f"hold load ID 1761000000.1, the current load ID is {LOAD_ID}")
    ):
        run(store=None, max_run=4)
