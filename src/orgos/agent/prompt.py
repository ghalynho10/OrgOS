"""The instruction text both stores share, and the question block of one run (spec 0012).

The instruction text is written once. The two stores fill the same five
dialect fields and the preview row count into it and nothing else, so a score
difference cannot come from different words (AC-3, AC-7). The schema
description is a second instructions entry, rendered by `describe_sql` and
`describe_graph`. `render_question_block` is the one rule for what the model
sees of a question (AC-4); `render_plain_block` is the block of `orgos ask`,
which holds only the question text (AC-5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from orgos.eval.handoff import Store
from orgos.eval.models import Question

# A reading note line with this prefix is never shown to the agent (AC-4).
HIDDEN_LINE_PREFIX = "If empty:"


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
is one, and often a reading note. The reading note defines every term of the question and \
says which records are the sources. Follow it exactly.
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
- path: one row per path, column path holding the ordered list of IDs of that path.
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
An aggregate answer has one step. A path answer has one step whose columns, in the result \
that holds the paths, hold for each path one item per hop. List every record that supports \
the answer, no more and no fewer, and exactly the kinds the reading note names. Run a query \
that returns those records, then point at its ID column. If the answer is empty, zero or \
false, list no sources at all. If the answer is not empty and the reading note names a kind \
for which there are no records, point at an empty result for it. Without a reading note, list \
the records you counted or returned."""


def render_instructions(store: Store, max_rows: int) -> str:
    """The shared instruction text with the dialect fields of `store` and the preview size."""
    return INSTRUCTIONS.format(MAX_ROWS=max_rows, **asdict(DIALECTS[store]))


def visible_reading(reading: str) -> str:
    """The reading note word for word, without every line that starts with `If empty:`."""
    return "\n".join(
        line for line in reading.splitlines() if not line.startswith(HIDDEN_LINE_PREFIX)
    )


def render_question_block(question: Question) -> str:
    """What the model sees of a scored question (AC-4).

    The text, the shape, the unit unless it is `none`, the hop limit only for a
    `variable` question, and the reading note without its `If empty:` lines.
    Never `type`, `hops`, `data`, `tracer` or `fields`.
    """
    lines = [f"Question: {question.text}", f"Answer shape: {question.shape}"]
    if question.unit != "none":
        lines.append(f"Unit: {question.unit}")
    if question.hop_mode == "variable" and question.max_hops is not None:
        lines.append(f"Hop limit: {question.max_hops}")
    lines.extend(("Reading note:", visible_reading(question.reading)))
    return "\n".join(lines)


def render_plain_block(text: str) -> str:
    """The block of a plain question (`orgos ask`): the question text and nothing else (AC-5)."""
    return f"Question: {text}"
