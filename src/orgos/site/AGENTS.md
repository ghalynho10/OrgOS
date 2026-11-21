# site

## Overview

The public results page (spec 0016): one static page at `https://ghalynho10.github.io/OrgOS/` that carries the engineer's Phase 1 conclusion word for word and the evidence tables from `results/findings/report.json`. `uv run orgos site build` writes `site/index.html` and `site/style.css`; `uv run orgos site check` proves the committed page is safe to publish. Neither opens a store, calls a model, loads `Settings` or makes a network call, so both run in CI with no `.env`. Every expected failure is a `SiteError` (exit 8).

## Key files

| File | Owns |
| --- | --- |
| `build.py` | The only file reads and writes: `build` (the conclusion copy, the page, the stylesheet) and `check` (regeneration, numbers, scan) |
| `conclusion.py` | Parses `site/conclusion.md` with `markdown-it-py` (tables on, raw HTML off) into the title, six sections and the caveat sentences (pure) |
| `evidence.py` | The report fields the page shows, in the fixed order of AC-30, and every refusal of a report that is partial, stale or out of line (pure) |
| `render.py` | Joins the sections, the tables and the constants into the page, in the order of AC-3 (pure) |
| `text.py` | Every word the page adds: K1 to K4, K7 to K12, L1 to L7, and `CANONICAL_URL` |
| `style.py` | The stylesheet, written from the spec's *Page design* tokens |
| `numbers.py` | The AC-9 numbers check: each number in the visible text must come from a report field the page reads, the conclusion or a constant |
| `scan.py` | The email pattern, the name list from four sources, and `ALLOWLIST` |

## Inputs and outputs

- `build` reads `docs/findings/conclusion.md`, `results/findings/report.json` and the tracked `site/conclusion.md`, and writes only `site/index.html`, `site/style.css` and `site/conclusion.md` (AC-1).
- The page is built from the tracked copy `site/conclusion.md`, never from `docs/`, because the docs repo is private and absent in CI. A changed `docs/findings/conclusion.md` is refused until `build --accept` copies it in; read that one diff before committing (AC-7).
- `check` also reads `tests/fixtures/corpus/*.json` and `eval/data-description.md` to build the name list. A scratch root given with `--root` needs them; `copy_inputs` in `tests/site_pages.py` copies exactly what is needed.
- Only `index.html` and `style.css` are published; `pages.yml` runs `orgos site check` first and deploys on a push to `main` that changes `site/**` or `results/findings/report.json`, or on `workflow_dispatch` (AC-24).

## Conventions

- Every word the page adds lives in `text.py`, and `tests/test_site_constants.py` pins each one to the string in spec 0016 *Page text*. A wording change is a spec amendment first, then `text.py` and the test.
- Numbers on the page come only from the report fields named in the spec's *Value sourcing*, the conclusion or the constants. A new table cell needs its field added to `texts()` in `evidence.py` (which `sources()` in `build.py` reads), or the numbers check fails.
- No cell, row, class or style may mark one number, store or table over another (AC-11). `tests/test_site_css.py` enforces the selector deny list (AC-49), the weights (AC-46) and the three font stacks (AC-47).
- The page shows no load id, hash or timestamp (AC-14), no script, and makes no request to another host (AC-39).
- Of the report's `limitations`, the page shows only `G2-6`; section 5 ends with the K12 link to the full list on `main` (AC-12, AC-44).

## Gotchas

- The scan prints the list size, the count per source, and a match by file, line and entry number, never the entry's text. Keep it that way in any new message or test; the tests use invented names only.
- An entry is allowed only by its SHA 256 in `ALLOWLIST`, added together with a dated line in spec 0016 *The scan*. Never add an entry's text anywhere.
- A source that yields no entry fails the check, and the eight person field headings of `eval/data-description.md` must all be found (AC-17, AC-43). A change to that file's heading format breaks the check on purpose.
- The tests read the tracked `site/conclusion.md` and the real report, so a change to either changes what the page tests expect; rebuild and commit `site/` with it (see the root rule).

## Related specs

- [0016 public results page](../../../docs/specs/0016-public-results-page/index.md): the page, its text, its design, the scan and the publish workflow
- [0014 findings report](../../../docs/specs/0014-findings-report/index.md): the `report.json` fields the page reads

_Drafted by /sync from the introducing change, worth a quick human pass._
