"""The shared instruction text and the question block (spec 0012 AC-3, AC-4, AC-7; spec 0015).

Spec 0015 adds the `Sources layout` line after the reading note (AC-1 to AC-3).
The layout tests read `layouts.toml` and the questions' ids and shapes, never
an answer value.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

import pytest

from orgos.agent.answer import SOURCE_KINDS
from orgos.agent.errors import AgentError
from orgos.agent.prompt import (
    DIALECTS,
    HIDDEN_LINE_PREFIX,
    LAYOUTS_FILE,
    PATH_LAYOUT_LINE,
    parse_layouts,
    render_instructions,
    render_layout_line,
    render_plain_block,
    render_question_block,
)
from orgos.eval.models import Question, read_toml

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = tuple(
    Question(**table) for table in read_toml(ROOT / "eval/questions.toml")["question"]
)
SHAPES = {question.id: question.shape for question in QUESTIONS}
LAYOUTS = parse_layouts(LAYOUTS_FILE.read_text(encoding="utf-8"), SHAPES)
SECTIONS = ("How you work", "Reading rules", "Answer shapes", "Sources")
# The three forms of the line (spec 0015 AC-2): one step, several steps, a path.
_KINDS = rf"(?:{'|'.join(SOURCE_KINDS)})(?:, (?:{'|'.join(SOURCE_KINDS)}))*"
LAYOUT_LINE = re.compile(
    rf"Sources layout: (?:1 step: {_KINDS}"
    rf"|(?P<n>[2-9]) steps: \(1\) {_KINDS}(?:; \([2-9]\) {_KINDS})+"
    rf"|{re.escape(PATH_LAYOUT_LINE.removeprefix('Sources layout: '))})"
)


def _placeholders(text: str, store: str) -> str:
    # Longest first: the graph's MISSING_RULE holds its STATEMENT_KIND word.
    fields = asdict(DIALECTS[store])  # type: ignore[index]
    for name, value in sorted(fields.items(), key=lambda item: -len(item[1])):
        text = text.replace(value, f"<{name}>")
    return text


def _sections(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    name = "opening"
    for line in text.splitlines():
        if line in SECTIONS:
            name = line
            continue
        found[name] = found.get(name, "") + line + "\n"
    return found


def test_the_two_texts_differ_only_in_the_five_dialect_fields() -> None:
    sql, graph = render_instructions("sql", 50), render_instructions("graph", 50)
    assert sql != graph
    assert _placeholders(sql, "sql") == _placeholders(graph, "graph")
    for name, value in asdict(DIALECTS["sql"]).items():
        assert value in sql, name
        assert asdict(DIALECTS["graph"])[name] in graph, name


def test_every_field_is_filled_and_the_preview_size_is_the_setting() -> None:
    for store in ("sql", "graph"):
        text = render_instructions(store, 50)  # type: ignore[arg-type]
        assert "{" not in text
        assert "}" not in text
        assert "a preview of its first 50 rows" in text
    assert "first 7 rows" in render_instructions("sql", 7)


def test_answer_shapes_and_sources_are_byte_for_byte_the_same() -> None:
    sql = _sections(render_instructions("sql", 50))
    graph = _sections(render_instructions("graph", 50))
    assert sql.keys() == graph.keys() == {"opening", *SECTIONS}
    assert sql["Answer shapes"] == graph["Answer shapes"]
    assert sql["Sources"] == graph["Sources"]
    assert "set: one row per distinct entity, column id." in sql["Answer shapes"]
    assert "not pairs" in sql["Answer shapes"]


def test_reading_rules_differ_only_in_the_dialect_lines() -> None:
    sql = _sections(render_instructions("sql", 50))["Reading rules"].splitlines()
    graph = _sections(render_instructions("graph", 50))["Reading rules"].splitlines()
    assert len(sql) == len(graph)
    differing = [(a, b) for a, b in zip(sql, graph, strict=True) if a != b]
    assert len(differing) == 3
    sql_fields, graph_fields = asdict(DIALECTS["sql"]), asdict(DIALECTS["graph"])
    for name in ("MISSING_RULE", "DATE_RULE", "LINK_RULE"):
        assert any(sql_fields[name] in a and graph_fields[name] in b for a, b in differing), name


# --- The three edits of version 2 (spec 0015 AC-6, AC-7) ---

V2_SENTENCES = {
    "How you work": (
        "The reading note defines every term of the question. Follow it exactly. When the block"
        " has a Sources layout line, that line says which kinds to list."
    ),
    "Sources": (
        "A step maps each kind it holds to one result column. When the question block has a"
        " Sources layout line, build exactly that layout: its steps in its order, each step"
        " holding exactly its kinds, and no other kind. Each kind may point at its own result. A"
        " source column holds no NULL, so query a kind separately when some rows lack it. A path"
        " answer has one step whose columns, in the result that holds the paths, hold for each"
        " path one item per hop: an ID, or a list of IDs when the hop holds several records of"
        " that kind. List every record that supports the answer, no more and no fewer. Run a"
        " query that returns those records, then point at its ID column. If the answer is empty,"
        " zero or false, list no sources at all. If the answer is not empty and a kind in the"
        " layout has no records, point at an empty result for it. Without a layout line, list the"
        " records you counted or returned."
    ),
    "Answer shapes": (
        "- path: one row per path, column path holding the ordered list of IDs of that path."
        " When the question lets a path follow links in either direction, return each chain once"
        " per direction, as two rows."
    ),
}
V1_PHRASES = (
    "exactly the kinds the reading note names",
    "says which records are the sources",
    "An aggregate answer has one step.",
    "the reading note names a kind",
    "Without a reading note",
)


@pytest.mark.parametrize("store", ["sql", "graph"])
def test_the_three_edits_are_in_and_the_old_wording_is_gone(store: str) -> None:
    sections = _sections(render_instructions(store, 50))  # type: ignore[arg-type]
    for name, sentence in V2_SENTENCES.items():
        assert sentence in " ".join(sections[name].split()), name
    text = " ".join(render_instructions(store, 50).split())  # type: ignore[arg-type]
    for phrase in V1_PHRASES:
        assert phrase not in text, phrase


def test_the_two_direction_rule_is_one_shared_sentence() -> None:
    rule = "return each chain once per direction, as two rows"
    for store in ("sql", "graph"):
        assert render_instructions(store, 50).replace("\n", " ").count(rule) == 1  # type: ignore[arg-type]


# --- The question block (AC-4) ---


def _expected(question: Question) -> list[str]:
    lines = [f"Question: {question.text}", f"Answer shape: {question.shape}"]
    if question.unit != "none":
        lines.append(f"Unit: {question.unit}")
    if question.hop_mode == "variable":
        lines.append(f"Hop limit: {question.max_hops}")
    lines.append("Reading note:")
    lines.extend(
        line for line in question.reading.splitlines() if not line.startswith(HIDDEN_LINE_PREFIX)
    )
    lines.append(render_layout_line(LAYOUTS[question.id], question.shape))
    return lines


def _block(question: Question) -> str:
    return render_question_block(question, LAYOUTS[question.id])


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_the_block_is_exactly_the_allowed_parts(question: Question) -> None:
    assert _block(question).splitlines() == _expected(question)


def test_the_if_empty_lines_are_gone_and_every_other_line_is_kept() -> None:
    with_hidden = [q for q in QUESTIONS if HIDDEN_LINE_PREFIX in q.reading]
    assert with_hidden
    for question in QUESTIONS:
        block = _block(question).rsplit("\n", 1)[0]
        note = block.split("Reading note:\n", 1)[1].splitlines()
        assert not any(line.startswith(HIDDEN_LINE_PREFIX) for line in note), question.id
        kept = [
            line
            for line in question.reading.splitlines()
            if not line.startswith(HIDDEN_LINE_PREFIX)
        ]
        assert note == kept, question.id
        hidden = len(question.reading.splitlines()) - len(kept)
        assert hidden == (1 if question in with_hidden else 0), question.id


def test_only_a_variable_question_prints_its_hop_limit() -> None:
    variable = [q for q in QUESTIONS if q.hop_mode == "variable"]
    fixed = [q for q in QUESTIONS if q.hop_mode == "fixed"]
    assert variable
    assert fixed
    assert all("Hop limit:" in _block(q) for q in variable)
    assert not any("Hop limit:" in _block(q) for q in fixed)


def test_type_hops_data_tracer_and_fields_are_never_printed() -> None:
    question = QUESTIONS[0].model_copy(
        update={"fields": ("fields.marker-path",), "reading": "Plain note."}
    )
    block = _block(question)
    assert "fields.marker-path" not in block
    for word in ("Type:", "Hops:", "Data:", "Tracer:", "Fields:", "aggregate", "real", "True"):
        assert word not in block


def test_a_none_unit_is_not_printed() -> None:
    none_unit = [q for q in QUESTIONS if q.unit == "none"]
    assert none_unit
    assert not any("Unit:" in _block(q) for q in none_unit)


def test_the_plain_block_holds_only_the_question_text() -> None:
    assert render_plain_block("How many projects?") == "Question: How many projects?"


# --- The layout lines (spec 0015 AC-1 to AC-3) ---


def test_the_layout_file_has_one_entry_per_question_and_the_path_ones_are_fixed() -> None:
    assert set(LAYOUTS) == set(SHAPES)
    assert len(LAYOUTS) == 20
    paths = sorted(qid for qid, shape in SHAPES.items() if shape == "path")
    assert paths == ["Q08", "Q09"]
    assert all(LAYOUTS[qid] == (("issue", "issue_link"),) for qid in paths)


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_the_layout_line_is_the_last_line_right_after_the_note(question: Question) -> None:
    lines = _block(question).splitlines()
    assert lines[-1].startswith("Sources layout: ")
    assert sum(line.startswith("Sources layout:") for line in lines) == 1
    note = _expected(question)[:-1]
    assert lines[:-1] == note
    assert LAYOUT_LINE.fullmatch(lines[-1]), lines[-1]


def test_the_three_forms_of_the_line() -> None:
    assert render_layout_line((("issue", "time_entry"),), "map") == (
        "Sources layout: 1 step: issue, time_entry"
    )
    assert render_layout_line((("issue", "issue_link"), ("person", "time_entry")), "map") == (
        "Sources layout: 2 steps: (1) issue, issue_link; (2) person, time_entry"
    )
    assert render_layout_line((("issue", "issue_link"),), "path") == (
        "Sources layout: 1 step, one item per hop: issue, issue_link. In each hop, issue holds"
        " both issues of the hop, issue_link holds every link between them."
    )


def test_the_plain_block_has_no_layout_line() -> None:
    for question in QUESTIONS:
        assert "Sources layout" not in render_plain_block(question.text)


SHAPES_TWO = {"Q01": "map", "Q08": "path"}


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('Q01 = [["issue"]]', "Q08: no layout"),
        ('Q01 = [["issue"]]\nQ08 = [["issue", "issue_link"]]\nQ99 = [["issue"]]', "Q99: not one"),
        ('Q01 = [["issues"]]\nQ08 = [["issue", "issue_link"]]', "'issues' is not one of"),
        ('Q01 = [["issue", "issue"]]\nQ08 = [["issue", "issue_link"]]', "listed twice"),
        ('Q01 = [["time_entry", "issue"]]\nQ08 = [["issue", "issue_link"]]', "alphabetical"),
        ('Q01 = [["issue"], []]\nQ08 = [["issue", "issue_link"]]', "step 2: the step is empty"),
        ('Q01 = []\nQ08 = [["issue", "issue_link"]]', "one or more steps"),
        ('Q01 = [["issue"]]\nQ08 = [["issue"]]', "Q08: a path question's layout is exactly"),
        (
            'Q01 = [["issue"]]\nQ08 = [["issue", "issue_link"], ["person"]]',
            "Q08: a path question's layout is exactly",
        ),
        ('Q01 = [["issue"]', "not valid TOML"),
    ],
    ids=[
        "missing",
        "unknown id",
        "unknown kind",
        "repeated kind",
        "unsorted",
        "empty step",
        "no steps",
        "path one kind",
        "path two steps",
        "bad toml",
    ],
)
def test_parse_layouts_refuses(text: str, problem: str) -> None:
    with pytest.raises(AgentError, match=re.escape(problem)):
        parse_layouts(text, SHAPES_TWO)  # type: ignore[arg-type]


def test_parse_layouts_keeps_the_order_of_steps() -> None:
    text = (
        'Q01 = [["issue", "issue_link"], ["person", "time_entry"]]\nQ08 = [["issue", "issue_link"]]'
    )
    layouts = parse_layouts(text, SHAPES_TWO)  # type: ignore[arg-type]
    assert layouts["Q01"] == (("issue", "issue_link"), ("person", "time_entry"))
