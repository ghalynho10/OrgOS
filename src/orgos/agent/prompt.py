"""The instruction text both stores share, and the question block of one run (spec 0012).

The instruction text is written once. The two stores fill the same five
dialect fields and the preview row count into it and nothing else, so a score
difference cannot come from different words (AC-3, AC-7). The schema
description is a second instructions entry, rendered by `describe_sql` and
`describe_graph`. `render_question_block` is the one rule for what the model
sees of a question (AC-4); `render_plain_block` is the block of `orgos ask`,
which holds only the question text (AC-5). Spec 0015 adds one `Sources layout`
line after the reading note, from `layouts.toml` by question id, so the plain
block never holds one (AC-1 to AC-3). `render_plain_run_block` is the block of
a plain run (spec 0013): the scored block cut before its reading note.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from orgos.agent.answer import SOURCE_KINDS, Layout
from orgos.agent.errors import AgentError
from orgos.eval.handoff import Store
from orgos.eval.models import Question, Shape

# A reading note line with this prefix is never shown to the agent (AC-4).
HIDDEN_LINE_PREFIX = "If empty:"
LAYOUTS_FILE = Path(__file__).with_name("layouts.toml")
# The one layout a path question may have, and its line (spec 0015 AC-1, AC-2).
PATH_LAYOUT: Layout = (("issue", "issue_link"),)
PATH_LAYOUT_LINE = (
    "Sources layout: 1 step, one item per hop: issue, issue_link. In each hop, issue holds"
    " both issues of the hop, issue_link holds every link between them."
)


@dataclass(frozen=True)
class Dialect:
    """The five fields of the instruction text that differ between the stores (AC-3)."""

    STORE_NAME: str
    STATEMENT_KIND: str
    MISSING_RULE: str
    DATE_RULE: str
    LINK_RULE: str


DIALECTS: Mapping[Store, Dialect] = {
    "sql": Dialect(
        STORE_NAME="PostgreSQL (schema model)",
        STATEMENT_KIND="SQL SELECT",
        MISSING_RULE=(
            "A missing value is NULL. Missing and empty text mean the same thing, so test both."
        ),
        DATE_RULE=(
            "Write bounds as timestamptz literals with an explicit +00 offset, never a named zone."
        ),
        LINK_RULE=(
            "In issue_link a row reads: from_issue_key outward_label to_issue_key, and"
            " backwards: to_issue_key inward_label from_issue_key. from_issue_key is the issue"
            " that holds the outward wording, not Jira's outwardIssue."
        ),
    ),
    "graph": Dialect(
        STORE_NAME="Neo4j (Cypher 25)",
        STATEMENT_KIND="Cypher",
        MISSING_RULE=(
            "A missing value is an absent property, which Cypher reads as null. Missing and"
            " empty text mean the same thing, so test both."
        ),
        DATE_RULE="Write bounds as datetime values in the Z form, never a named zone.",
        LINK_RULE=(
            "A link relationship runs from the issue that holds the outward wording to the other"
            " issue. Read forward it says outwardLabel, read backwards inwardLabel."
        ),
    ),
}

# The shared text of the Feature design, word for word. The capitals in braces
# are the dialect fields; MAX_ROWS is the preview setting, the same for both.
INSTRUCTIONS = """\
You answer one question about a company's Jira data by querying a {STORE_NAME} database. \
You can only read. The data is a snapshot of 7 projects (BAM, CLOUD, CLOV, CRUC, CWD, FE, \
JRASERVER). Flagged bot accounts are already removed. Time entries are already rebuilt from \
the changelog; every other value is the current one.

How you work
1. The question block gives the question, the answer shape and unit, a hop limit when there \
is one, and often a reading note. The reading note defines every term of the question. Follow \
it exactly. When the block has a Sources layout line, that line says which kinds to list.
2. Call run_query to explore and to compute. One {STATEMENT_KIND} statement per call. Each \
call returns a result handle (r1, r2, ...), its columns, its row count and a preview of its \
first {MAX_ROWS} rows. The tool keeps the full result. Return plain values (a key, a name, a \
number, a date), never whole nodes, relationships, paths or maps.
3. When you are sure, call final_answer. It points at results. Never type an answer value or \
an ID list yourself, and never copy IDs out of a preview.
4. If you cannot answer, stop and say why in plain text.

Reading rules
- Count with count, never sum. A count of nothing is 0, not missing. Count distinct whenever \
a join can repeat a record.
- {MISSING_RULE}
- All times are UTC. "Between A and B" is half open: A included, B excluded. {DATE_RULE}
- Logged time counts only entries with seconds above 0. Give every duration in seconds, never \
in hours or days, whatever the question says.
- {LINK_RULE}
- A question with a hop limit binds you: never follow a path longer than the limit, never \
repeat an issue within a path, and return only complete chains.
- Answer the question as written. An empty or zero result is the answer. Do not widen the \
question.
- The preview is a sample. Decide from counts and totals, not from the preview.

Answer shapes
Build the result you point at with exactly these columns.
- number: exactly one row, column value.
- boolean: exactly one row, column value, true or false.
- map: one row per entity, columns id and value.
- ranking: one row per entity in rank order, best first, columns id and value. Keep tied \
entities as separate neighbouring rows.
- set: one row per distinct entity, column id. Return the entities the question asks for, \
not pairs. Supporting connections belong in the sources.
- path: one row per path, column path holding the ordered list of IDs of that path. When the \
question lets a path follow links in either direction, return each chain once per direction, \
as two rows.
In final_answer, name that result (answer_result), and also the kind of the IDs (id_kind). \
When one answer mixes kinds (people and projects in one set), add a column kind to the result \
and leave id_kind out.

Sources
A source is a record that supports the answer. List the sources as steps. Each step maps a \
kind to a column of a result that holds those IDs. The kinds and their IDs:
- issue: the issue key
- project: the project key
- person: the person key
- time_entry: the time entry id
- issue_link: the link id
- parent: the key of the child issue that holds a parent relation
A step maps each kind it holds to one result column. When the question block has a Sources \
layout line, build exactly that layout: its steps in its order, each step holding exactly its \
kinds, and no other kind. Each kind may point at its own result. A source column holds no \
NULL, so query a kind separately when some rows lack it. A path answer has one step whose \
columns, in the result that holds the paths, hold for each path one item per hop: an ID, or a \
list of IDs when the hop holds several records of that kind. List every record that supports \
the answer, no more and no fewer. Run a query that returns those records, then point at its ID \
column. If the answer is empty, zero or false, list no sources at all. If the answer is not \
empty and a kind in the layout has no records, point at an empty result for it. Without a \
layout line, list the records you counted or returned."""


def render_instructions(store: Store, max_rows: int) -> str:
    """The shared instruction text with the dialect fields of `store` and the preview size."""
    return INSTRUCTIONS.format(MAX_ROWS=max_rows, **asdict(DIALECTS[store]))


def visible_reading(reading: str) -> str:
    """The reading note word for word, without every line that starts with `If empty:`."""
    return "\n".join(
        line for line in reading.splitlines() if not line.startswith(HIDDEN_LINE_PREFIX)
    )


def _step_problems(qid: str, steps: Any) -> list[str]:
    if not isinstance(steps, list) or not steps:
        return [f"{qid}: the layout must be a list of one or more steps"]
    problems = []
    for number, step in enumerate(steps, start=1):
        where = f"{qid}, step {number}"
        if not isinstance(step, list) or not all(isinstance(kind, str) for kind in step):
            problems.append(f"{where}: a step must be a list of kinds")
            continue
        if not step:
            problems.append(f"{where}: the step is empty")
        problems.extend(
            f"{where}: {kind!r} is not one of {', '.join(SOURCE_KINDS)}"
            for kind in step
            if kind not in SOURCE_KINDS
        )
        if len(set(step)) != len(step):
            problems.append(f"{where}: a kind is listed twice")
        elif step != sorted(step):
            problems.append(f"{where}: the kinds are not in alphabetical order")
    return problems


def parse_layouts(text: str, shapes: Mapping[str, Shape]) -> Mapping[str, Layout]:
    """The layout of each question in `layouts.toml` text; raises `AgentError` naming every problem.

    `shapes` maps each of the 20 question ids to its shape. Every question
    needs an entry and no other id may have one; each step is a non empty,
    alphabetical list of distinct source kinds; a `path` question's layout is
    exactly one step `issue, issue_link` (spec 0015 AC-1).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise AgentError(f"{LAYOUTS_FILE.name} is not valid TOML: {error}") from None
    problems = [f"{qid}: no layout" for qid in sorted(shapes) if qid not in data]
    problems.extend(
        f"{qid}: not one of the 20 questions" for qid in sorted(data) if qid not in shapes
    )
    for qid in sorted(set(data) & set(shapes)):
        step_problems = _step_problems(qid, data[qid])
        problems.extend(step_problems)
        is_path = not step_problems and shapes[qid] == "path"
        if is_path and tuple(tuple(step) for step in data[qid]) != PATH_LAYOUT:
            problems.append(f"{qid}: a path question's layout is exactly [[issue, issue_link]]")
    if problems:
        raise AgentError(f"{LAYOUTS_FILE.name} is not usable:\n  " + "\n  ".join(problems))
    return {qid: tuple(tuple(step) for step in data[qid]) for qid in sorted(shapes)}


def render_layout_line(layout: Layout, shape: Shape) -> str:
    """The `Sources layout` line of a scored question block (spec 0015 AC-2)."""
    if shape == "path":
        return PATH_LAYOUT_LINE
    if len(layout) == 1:
        return f"Sources layout: 1 step: {', '.join(layout[0])}"
    steps = "; ".join(f"({n}) {', '.join(kinds)}" for n, kinds in enumerate(layout, start=1))
    return f"Sources layout: {len(layout)} steps: {steps}"


def render_question_block(question: Question, layout: Layout) -> str:
    """What the model sees of a scored question (AC-4, amended by spec 0015 AC-2).

    The text, the shape, the unit unless it is `none`, the hop limit only for a
    `variable` question, the reading note without its `If empty:` lines, and
    last the question's `Sources layout` line. Never `type`, `hops`, `data`,
    `tracer` or `fields`.
    """
    lines = [f"Question: {question.text}", f"Answer shape: {question.shape}"]
    if question.unit != "none":
        lines.append(f"Unit: {question.unit}")
    if question.hop_mode == "variable" and question.max_hops is not None:
        lines.append(f"Hop limit: {question.max_hops}")
    lines.extend(
        (
            "Reading note:",
            visible_reading(question.reading),
            render_layout_line(layout, question.shape),
        )
    )
    return "\n".join(lines)


def render_plain_run_block(question: Question) -> str:
    """What the model sees of a question in a plain run (spec 0013 AC-1).

    The scored block cut before its reading note: the text, the shape, the unit
    unless it is `none`, and the hop limit only for a `variable` question, in
    the words and order of `render_question_block`. No reading note and so no
    `Sources layout` line. It never reads `question.reading`, and is not part
    of the version hash; spec 0013 AC-8 pins it.
    """
    lines = [f"Question: {question.text}", f"Answer shape: {question.shape}"]
    if question.unit != "none":
        lines.append(f"Unit: {question.unit}")
    if question.hop_mode == "variable" and question.max_hops is not None:
        lines.append(f"Hop limit: {question.max_hops}")
    return "\n".join(lines)


def render_plain_block(text: str) -> str:
    """The block of a plain question (`orgos ask`): the question text and nothing else (AC-5)."""
    return f"Question: {text}"
