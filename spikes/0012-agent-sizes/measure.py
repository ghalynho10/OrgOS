"""Spike 0012: measure what the agent sends, in o200k_base tokens (spec 0012 AC-28).

Checks both store sessions first, as `build_agent` does, then reads the
structure and value lists as the reader login and the read only graph,
renders every text the agent sends, and counts its tokens. Makes no model
call and writes only counts to `results/sizes.json`, never a store value.

    uv run --env-file .env python spikes/0012-agent-sizes/measure.py
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import tiktoken

from orgos.agent.agent import read_question_blocks, tool_definitions
from orgos.agent.describe_graph import read_graph_values, render_graph_description
from orgos.agent.describe_sql import read_sql_structure, read_sql_values, render_sql_description
from orgos.agent.meanings import load_meanings
from orgos.agent.prompt import render_instructions
from orgos.agent.session import check_sessions, read_graph_session, read_sql_session
from orgos.agent.tools import RunTrace, run_query
from orgos.config import load_settings
from orgos.graphstore.mapping import load_mapping
from orgos.graphstore.query import reader_driver, run_cypher
from orgos.sqlstore.query import reader_connection, run_sql

HERE = Path(__file__).parent
ENCODING = "o200k_base"
TOKENIZER_MARGIN = 1.25
# A full preview: 50 rows of 6 plain columns, the same columns in both stores.
PREVIEW_SQL = (
    "SELECT issue_key, status_name, issue_type_name, priority_name, created_at, resolution_date"
    " FROM model.issue ORDER BY issue_key LIMIT 200"
)
PREVIEW_CYPHER = (
    "CYPHER 25 MATCH (i:Issue) RETURN i.issueKey AS issue_key, i.statusName AS status_name,"
    " i.issueTypeName AS issue_type_name, i.priorityName AS priority_name,"
    " i.createdAt AS created_at, i.resolutionDate AS resolution_date"
    " ORDER BY issue_key LIMIT 200"
)
# The round sizes of the spec's estimate: a typical round, and a worst case
# round of one full preview plus the statement and the model's own text.
TYPICAL_ROUNDS, TYPICAL_ROUND = 8, 1_000
WORST_ROUNDS, WORST_EXTRA = 15, 300


def main() -> None:
    """Measure every part and write `results/sizes.json`."""
    settings = load_settings()
    timeout = float(settings.query_timeout_s)
    encoding = tiktoken.get_encoding(ENCODING)

    def tokens(text: str) -> int:
        return len(encoding.encode(text))

    mapping, meanings = load_mapping(), load_meanings()
    with reader_connection(settings) as conn, reader_driver(settings) as driver:
        check_sessions(
            read_sql_session(conn, timeout_s=timeout),
            read_graph_session(driver),
            reader_login=settings.pg_reader_user,
        )
        descriptions = {
            "sql": render_sql_description(
                read_sql_structure(conn, timeout_s=timeout),
                meanings,
                read_sql_values(conn, timeout_s=timeout),
            ).text,
            "graph": render_graph_description(
                mapping, meanings, read_graph_values(driver, mapping, timeout_s=timeout)
            ).text,
        }
        previews = {
            "sql": run_query(
                RunTrace("sql", lambda s: run_sql(conn, s, timeout_s=timeout), 50), PREVIEW_SQL
            ),
            "graph": run_query(
                RunTrace("graph", lambda s: run_cypher(driver, s, timeout_s=timeout), 50),
                PREVIEW_CYPHER,
            ),
        }

    blocks = read_question_blocks()
    block_tokens = [tokens(block) for block in blocks]
    largest = max(range(len(blocks)), key=lambda n: block_tokens[n])
    tools = tokens(json.dumps(tool_definitions(), sort_keys=True))
    stores: dict[str, dict[str, int]] = {}
    for store in ("sql", "graph"):
        instructions = tokens(render_instructions(store, settings.agent_max_rows))
        description = tokens(descriptions[store])
        preview = tokens(previews[store])
        opening = instructions + description + tools
        at_most = opening + block_tokens[largest]
        stores[store] = {
            "instructions": instructions,
            "description": description,
            "tools": tools,
            "opening_context": opening,
            "largest_question_block": block_tokens[largest],
            "opening_context_at_most": at_most,
            "with_tokenizer_margin": round(at_most * TOKENIZER_MARGIN),
            "full_preview": preview,
            "end_context_typical": at_most + TYPICAL_ROUNDS * TYPICAL_ROUND,
            "end_context_worst": at_most + WORST_ROUNDS * (preview + WORST_EXTRA),
        }
    result = {
        "measured_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "encoding": ENCODING,
        "max_rows": settings.agent_max_rows,
        "largest_question_block_id": f"Q{largest + 1:02d}",
        "question_blocks": len(blocks),
        "stores": stores,
    }
    out = HERE / "results" / "sizes.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
