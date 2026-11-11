# Spike 0012: how big is what the agent sends

This spike measures the built agent's texts with the `o200k_base` tokenizer, for the Local model fit table of spec 0012 (AC-28). `o200k_base` is an OpenAI tokenizer used as a proxy; another model's tokenizer can differ by about 25 percent, which is the margin row. The spike checked both store sessions first, read the stores only as the reader login and the read only graph, made no model call, and wrote only token counts.

Rerun it with `uv run --env-file .env python spikes/0012-agent-sizes/measure.py`. The numbers below are from `results/sizes.json`, measured on 2026-11-11 with `ORGOS_AGENT_MAX_ROWS=50`.

| Part | SQL | Graph |
|---|---|---|
| Instructions (the shared text with its dialect fields) | 908 | 898 |
| Schema description (live structure and value lists) | 2,086 | 2,045 |
| Tool and answer definitions (`run_query`, `final_answer`) | 525 | 525 |
| Opening context | 3,519 | 3,468 |
| Plus the largest question block (Q20) | 526 | 526 |
| Opening context at most | 4,045 | 3,994 |
| With a 25 percent tokenizer margin | 5,056 | 4,992 |
| One full preview (50 rows, 6 columns, from the live store) | 2,156 | 2,156 |
| End context of a typical run (8 rounds of about 1,000) | 12,045 | 11,994 |
| End context of a worst case run (15 rounds of one full preview plus 300) | 40,885 | 40,834 |

## What it means for a local model

- The opening context alone is above a 4,096 token window, so a local run needs the window raised and recorded.
- A typical run fits 16,384 tokens.
- The worst case is about 41,000 tokens. That is above 32,768, so a worst case run needs a window of 65,536, or it will be cut off.
- Both previews are the same size because one formatter renders both stores.

The two end context rows are estimates built from measured parts: the round counts and the extra 300 tokens per round are the spec's assumptions, not measurements.
