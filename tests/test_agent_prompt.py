"""The shared instruction text and the question block (spec 0012 AC-3, AC-4, AC-7)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from orgos.agent.prompt import (
    DIALECTS,
    HIDDEN_LINE_PREFIX,
    render_instructions,
    render_plain_block,
    render_question_block,
)
from orgos.eval.models import Question, read_toml

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = tuple(
    Question(**table) for table in read_toml(ROOT / "eval/questions.toml")["question"]
)
SECTIONS = ("How you work", "Reading rules", "Answer shapes", "Sources")


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
    return lines


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda question: question.id)
def test_the_block_is_exactly_the_allowed_parts(question: Question) -> None:
    assert render_question_block(question).splitlines() == _expected(question)


def test_the_if_empty_lines_are_gone_and_every_other_line_is_kept() -> None:
    with_hidden = [q for q in QUESTIONS if HIDDEN_LINE_PREFIX in q.reading]
    assert with_hidden
    for question in QUESTIONS:
        block = render_question_block(question)
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
    assert all("Hop limit:" in render_question_block(q) for q in variable)
    assert not any("Hop limit:" in render_question_block(q) for q in fixed)


def test_type_hops_data_tracer_and_fields_are_never_printed() -> None:
    question = QUESTIONS[0].model_copy(
        update={"fields": ("fields.marker-path",), "reading": "Plain note."}
    )
    block = render_question_block(question)
    assert "fields.marker-path" not in block
    for word in ("Type:", "Hops:", "Data:", "Tracer:", "Fields:", "aggregate", "real", "True"):
        assert word not in block


def test_a_none_unit_is_not_printed() -> None:
    none_unit = [q for q in QUESTIONS if q.unit == "none"]
    assert none_unit
    assert not any("Unit:" in render_question_block(q) for q in none_unit)


def test_the_plain_block_holds_only_the_question_text() -> None:
    assert render_plain_block("How many projects?") == "Question: How many projects?"
