"""Unit tests for the pure core of `orgos eval describe` and the reader only rule.

Spec 0005, AC-13 to AC-16 and AC-18. No store: the counts, the log block and
the rendered file are checked on synthetic rows; the reader only rule by
reading the source of `src/orgos/eval/`.
"""

import datetime as dt
import re
from pathlib import Path

import pytest

from orgos.eval import EvalError
from orgos.eval.checker import read_field_lists
from orgos.eval.describe import (
    CORPUS_FILE,
    Corpus,
    Description,
    Kept,
    LinkRow,
    Negative,
    corpus_queries,
    document_shape,
    fill_counts,
    kept_and_dropped,
    link_row,
    log_block,
    negative_entries,
    percent,
    read_corpus,
    render,
)
from orgos.eval.entries import TimeEntry
from orgos.eval.fields import ALWAYS_USABLE, field_classes, summarize, walked_values

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Corpus("Jira", ("AAA", "BBB"))
AT = dt.datetime(2026, 10, 29, 12, 30, 45, 999, tzinfo=dt.UTC)
CATEGORIES = (
    "shape",
    "fill_rate",
    "distinct_values",
    "exception:links_parents",
    "exception:negative_entries",
)


def test_the_corpus_is_the_spike_group() -> None:
    corpus = read_corpus(ROOT / CORPUS_FILE)
    assert corpus.collection == "Jira"
    assert len(corpus.projects) == 7


def test_a_missing_corpus_file_stops(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="not found"):
        read_corpus(tmp_path / "corpus.json")


def test_the_queries_cover_the_ac13_categories_in_order() -> None:
    queries = corpus_queries(CORPUS).all()
    assert tuple(q.category for q in queries) == CATEGORIES
    for query in queries:
        assert query.filter == {"fields.project.key": {"$in": ["AAA", "BBB"]}}
        assert "renderedFields" not in query.text


def test_a_log_block_has_the_time_category_and_text_only() -> None:
    query = corpus_queries(CORPUS).fill_rate
    block = log_block(query, AT)
    lines = block.splitlines()
    assert lines[0] == "## 2026-10-29T12:30:45Z · fill_rate"
    assert lines[2] == "```"
    assert lines[3] == query.text
    assert lines[4] == "```"
    assert re.search(r"mongodb://|password|orgos_raw_reader", block) is None


def test_a_log_block_time_is_utc() -> None:
    paris = dt.timezone(dt.timedelta(hours=1))
    block = log_block(corpus_queries(CORPUS).shape, dt.datetime(2026, 1, 1, 0, 30, tzinfo=paris))
    assert block.startswith("## 2025-12-31T23:30:00Z · shape")


def test_fill_counts_per_project() -> None:
    issues, filled = fill_counts(
        [("AAA", frozenset({"a", "b"})), ("AAA", frozenset({"a"})), ("BBB", frozenset())]
    )
    assert issues == {"AAA": 2, "BBB": 1}
    assert filled == {"a": {"AAA": 2}, "b": {"AAA": 1}}


def test_a_filled_path_ignores_empty_values() -> None:
    doc = {"key": "A-1", "id": "1", "fields": {"a": "", "b": [], "c": None, "d": 0}}
    assert {path for path, _ in walked_values(doc)} == {"key", "id", "fields.d"}


@pytest.mark.parametrize(
    ("filled", "total", "shown"),
    [(0, 10, "0"), (10, 10, "100"), (1, 3, "33.3"), (1, 10_000, "<0.1"), (9_999, 10_000, ">99.9")],
)
def test_percent_is_exact_at_both_ends(filled: int, total: int, shown: str) -> None:
    assert percent(filled, total) == shown


def issue_with_links(key: str, project: str, *links: dict[str, object], parent: str = "") -> dict:
    fields: dict[str, object] = {"project": {"key": project}, "issuelinks": list(links)}
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "fields": fields}


def test_links_and_parents_kept_or_dropped_by_corpus_membership() -> None:
    rows = [
        link_row(
            issue_with_links(
                "A-1",
                "AAA",
                {"id": "1", "outwardIssue": {"key": "B-1"}},
                {"id": "2", "inwardIssue": {"key": "OUT-1"}},
                parent="OUT-2",
            )
        ),
        link_row(
            issue_with_links("B-1", "BBB", {"id": "1", "inwardIssue": {"key": "A-1"}}, parent="A-1")
        ),
    ]
    kept = kept_and_dropped(rows)
    assert kept["AAA"] == Kept(links_kept=1, links_dropped=1, parents_kept=0, parents_dropped=1)
    assert kept["BBB"] == Kept(links_kept=1, links_dropped=0, parents_kept=1, parents_dropped=0)


def test_a_link_stored_twice_on_one_project_counts_once() -> None:
    rows = [
        LinkRow("AAA", "A-1", (("7", "A-2"),), None),
        LinkRow("AAA", "A-2", (("7", "A-1"),), None),
    ]
    assert kept_and_dropped(rows)["AAA"].links_kept == 1


def test_a_link_without_an_id_stops() -> None:
    with pytest.raises(EvalError, match="A-1"):
        link_row(issue_with_links("A-1", "AAA", {"outwardIssue": {"key": "B-1"}}))


def entry(project: str, amount: int, author: str | None = "p") -> TimeEntry:
    return TimeEntry("1", "A-1", project, author, AT, amount)


def test_negative_entries_per_project_only() -> None:
    found = negative_entries(
        [entry("AAA", -60), entry("AAA", -30, None), entry("AAA", 90), entry("BBB", 10)]
    )
    assert found == {"AAA": Negative(count=2, seconds=-90)}


def test_the_document_shape_holds_types_and_no_values() -> None:
    doc = {
        "key": "SECRET-1",
        "fields": {"summary": "secret words"},
        "changelog": {"histories": [{"id": "99", "items": [{"field": "status"}]}]},
    }
    shape = dict(document_shape(doc))
    assert shape["key"] == "string"
    assert shape["fields"] == "object"
    assert shape["changelog.histories"] == "array of object"
    assert shape["changelog.histories[].items[].field"] == "string"
    assert "SECRET" not in repr(shape) and "99" not in repr(shape)


def description(docs: list[dict]) -> Description:
    summaries = summarize(pair for doc in docs for pair in walked_values(doc))
    return Description(
        corpus=CORPUS,
        written_at=AT,
        shape=document_shape(docs[0]),
        classes=field_classes(summaries),
        value_lists=(("fields.status.name", ("Done", "Open")),),
        issues={"AAA": 2},
        filled={"fields.status.name": {"AAA": 1}},
        kept={"AAA": Kept(1, 0, 0, 0)},
        negative={},
    )


def test_the_rendered_lists_are_what_the_checker_reads() -> None:
    docs = [
        {"key": "A-1", "id": "1", "fields": {"status": {"name": "Open"}, "summary": "words"}},
        {"key": "A-2", "id": "2", "fields": {"status": {"name": "Done"}}},
    ]
    text = render(description(docs))
    lists = read_field_lists(text)
    assert lists.usable == {"key", "id", "fields.status.name", *ALWAYS_USABLE}
    assert lists.free_text == {"fields.summary"}
    for heading in (
        "## Corpus projects",
        "## Usable fields",
        "## Free text, not usable",
        "## Value lists",
        "## Fill rates per project",
        "## Issue links and parents kept or dropped",
        "## Negative time entries",
    ):
        assert f"\n{heading}\n" in text
    assert "there is no worklog field" in text
    assert "| `fields.status.name` | 50.0 | 0 | 50.0 |" in text
    assert "| BBB | 0 | 0 |" in text


# AC-18: reader only

EVAL_SOURCES = sorted((ROOT / "src" / "orgos" / "eval").glob("*.py"))
# Every pymongo call that writes, or that could (`aggregate` with `$out`, a raw command).
WRITES = re.compile(
    r"\.(insert_\w+|update_(one|many)|delete_(one|many)|replace_one|drop\w*|create_\w+|"
    r"bulk_write|find_one_and_\w+|aggregate|rename|command|with_options)\("
)


def test_the_eval_package_builds_only_reader_clients() -> None:
    assert EVAL_SOURCES
    for source in EVAL_SOURCES:
        text = source.read_text()
        assert "root_client" not in text, source
        assert "MongoClient" not in text, source
        assert "mongo_uri" not in text, source


def test_the_eval_package_makes_no_write_or_model_call() -> None:
    for source in EVAL_SOURCES:
        text = source.read_text()
        assert WRITES.search(text) is None, source
        assert re.search(r"pydantic_ai|anthropic|openai|httpx|requests", text) is None, source
