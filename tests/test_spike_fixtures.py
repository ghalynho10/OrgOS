"""Unit tests for the fixture draw and prose replacement of spike 0001 (spec 0004, AC-16 to AC-18).

`select_fixtures.py` is loaded by file path with the spike folder on the
import path (it imports `common`). No store is touched: the draw and the
replacement run on synthetic issues, and the committed fixtures are read from
`tests/fixtures/corpus/`.
"""

import copy
import importlib.util
import json
import random
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from bson import json_util

SPIKE = Path(__file__).resolve().parents[1] / "spikes" / "0001-time-data-coverage"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "corpus"


def _load() -> ModuleType:
    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    spec = importlib.util.spec_from_file_location(
        "spike0001_select_fixtures", SPIKE / "select_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sf = _load()
rules = sf.rules
PLACEHOLDER = rules.PLACEHOLDER


def cand(key: str, groups: tuple[str, ...] = (), links: tuple[tuple[int, str], ...] = ()) -> Any:
    project, number = key.split("-")
    return sf.Candidate(key, int(number), project, frozenset(groups), links)


def pool() -> list[Any]:
    out = []
    for project in ("AAA", "BBB", "CCC"):
        for n in range(1, 51):
            key = f"{project}-{n}"
            groups = ("timed",) if n <= 40 else ("no_time",)
            if n in (3, 43):
                groups = (*groups, "negative_entry")
            out.append(cand(key, groups))
    return out


# AC-16: the draw


def test_same_seed_same_draw_whatever_input_order() -> None:
    first = sf.draw(pool())
    shuffled = pool()
    random.Random(1).shuffle(shuffled)
    assert sf.draw(shuffled) == first
    assert sf.draw(list(reversed(pool()))) == first


def test_round_robin_alternates_projects_alphabetically_and_skips_exhausted() -> None:
    got = sf.round_robin({"B": ["B-1", "B-2", "B-3"], "A": ["A-1"]}, 4, ())
    assert got == ("A-1", "B-1", "B-2", "B-3")


def test_round_robin_skips_drawn_and_stops_at_quota() -> None:
    got = sf.round_robin({"A": ["A-1", "A-2"], "B": ["B-1", "B-2"]}, 2, ("A-1",))
    assert got == ("A-2", "B-1")


def test_short_group_is_recorded_not_refilled() -> None:
    picked = sf.draw(pool())
    groups = dict(picked.groups)
    assert len(groups["timed"]) == rules.QUOTA_TIMED
    assert len(groups["no_time"]) == 30  # 3 projects x 10, under the quota of 60
    assert groups["flagged_entry"] == ()
    assert ("no_time", rules.QUOTA_NO_TIME, 30) in picked.shortfalls
    assert ("flagged_entry", rules.QUOTA_FLAGGED, 0) in picked.shortfalls


def test_an_issue_is_never_drawn_twice() -> None:
    picked = sf.draw(pool())
    keys = [k for _, group in picked.groups for k in group]
    assert len(keys) == len(set(keys))
    negative = dict(picked.groups)["negative_entry"]
    timed = set(dict(picked.groups)["timed"])
    assert not set(negative) & timed
    assert len(picked.keys) == len(set(picked.keys))


def test_a_link_with_an_end_already_drawn_still_counts() -> None:
    issues = [
        cand("AAA-1", ("timed",), ((7, "BBB-1"),)),
        cand("BBB-1", ("timed",), ((7, "AAA-1"),)),
        cand("BBB-2", (), ((8, "AAA-1"),)),
    ]
    picked = sf.draw(issues)
    assert set(picked.links) == {(7, "AAA-1", "BBB-1"), (8, "AAA-1", "BBB-2")}
    assert ("links", rules.QUOTA_LINKS, 2) in picked.shortfalls
    assert "BBB-2" in picked.keys


def test_links_dedupe_by_id_and_need_both_ends_in_the_corpus() -> None:
    issues = [
        cand("AAA-1", (), ((7, "BBB-1"), (9, "ZZZ-1"))),
        cand("BBB-1", (), ((7, "AAA-1"),)),
    ]
    assert sf.corpus_links(issues) == ((7, "AAA-1", "BBB-1"),)


def _issue(**fields: Any) -> dict[str, Any]:
    histories = fields.pop("histories", [])
    return {"id": "101", "key": "AAA-1", "fields": fields, "changelog": {"histories": histories}}


def _entry(amount_from: str, amount_to: str, key: str = "u1") -> dict[str, Any]:
    item = {"field": "timespent", "fieldtype": "jira", "from": amount_from, "to": amount_to}
    return {"author": {"key": key}, "created": "2020-01-01T00:00:00.000+0000", "items": [item]}


def test_classify_puts_an_issue_in_each_group_it_meets() -> None:
    project = {"key": "AAA"}
    timed = _issue(project=project, timeoriginalestimate=3600, histories=[_entry("", "600")])
    assert sf.classify(timed, frozenset(), "key").groups == {"timed"}
    empty = _issue(project=project, timespent=0)
    assert sf.classify(empty, frozenset(), "key").groups == {"no_time"}
    logged_total_only = _issue(project=project, timespent=60)
    assert sf.classify(logged_total_only, frozenset(), "key").groups == set()
    mixed = _issue(
        project=project,
        timeoriginalestimate=3600,
        histories=[_entry("", "600", "bot"), _entry("600", "300")],
    )
    assert sf.classify(mixed, frozenset({"bot"}), "key").groups == {
        "flagged_entry",
        "negative_entry",
    }


# AC-17: prose replacement


def prose_issue() -> dict[str, Any]:
    return {
        "_id": "x",
        "key": "AAA-1",
        "fields": {
            "summary": "kept summary",
            "description": "secret text",
            "environment": None,
            "comment": {"total": 2, "comments": [{"id": "1", "body": "hi"}, {"body": None}]},
            "labels": ["kept"],
        },
        "renderedFields": {
            "description": "<p>secret</p>",
            "comment": {"comments": [{"body": "x"}]},
        },
        "changelog": {
            "histories": [
                {
                    "items": [
                        {"field": "description", "fromString": None, "toString": "new text"},
                        {"field": "summary", "fromString": "a", "toString": "b"},
                        {"field": "environment", "fromString": "old", "toString": ""},
                    ]
                }
            ]
        },
    }


def test_only_the_listed_paths_change() -> None:
    original = prose_issue()
    before = copy.deepcopy(original)
    out, paths = sf.strip_prose(original)
    assert original == before
    assert paths == (
        "fields.description",
        "fields.comment.comments[0].body",
        "renderedFields.description",
        "renderedFields.comment.comments[0].body",
        "changelog.histories[0].items[0].toString",
        "changelog.histories[0].items[2].fromString",
        "changelog.histories[0].items[2].toString",
    )
    expected = copy.deepcopy(before)
    expected["fields"]["description"] = PLACEHOLDER
    expected["fields"]["comment"]["comments"][0]["body"] = PLACEHOLDER
    expected["renderedFields"]["description"] = PLACEHOLDER
    expected["renderedFields"]["comment"]["comments"][0]["body"] = PLACEHOLDER
    items = expected["changelog"]["histories"][0]["items"]
    items[0]["toString"] = PLACEHOLDER
    items[2]["fromString"] = PLACEHOLDER
    items[2]["toString"] = PLACEHOLDER
    assert out == expected


def test_null_and_absent_stay_as_they_are() -> None:
    out, _ = sf.strip_prose(prose_issue())
    assert out["fields"]["environment"] is None
    assert out["fields"]["comment"]["comments"][1]["body"] is None
    assert "environment" not in out["renderedFields"]
    assert out["changelog"]["histories"][0]["items"][0]["fromString"] is None


def test_jira_comment_list_bodies_are_replaced() -> None:
    # AC-17 amendment: the Jira collection keeps comments as a list at fields.comments.
    author = {"key": "u1", "displayName": "kept name"}
    issue = {
        "key": "AAA-1",
        "fields": {
            "comments": [
                {"id": "1", "author": author, "body": "personal text", "created": "2020"},
                {"id": "2", "author": author, "body": None},
            ]
        },
    }
    out, paths = sf.strip_prose(issue)
    assert paths == ("fields.comments[0].body",)
    expected = copy.deepcopy(issue)
    expected["fields"]["comments"][0]["body"] = PLACEHOLDER
    assert out == expected


def test_removed_comment_items_lose_their_raw_text() -> None:
    # AC-17 amendment 2: a removed comment keeps its old text in the raw from of a Comment item.
    removed = {"field": "Comment", "fieldtype": "jira", "from": "old personal text", "to": None}
    other = {"field": "Status", "fieldtype": "jira", "from": "1", "fromString": "Open"}
    issue = {"key": "AAA-1", "changelog": {"histories": [{"items": [removed, other]}]}}
    out, paths = sf.strip_prose(issue)
    assert paths == ("changelog.histories[0].items[0].from",)
    expected = copy.deepcopy(issue)
    expected["changelog"]["histories"][0]["items"][0]["from"] = PLACEHOLDER
    assert out == expected


# AC-17, AC-18: the committed fixtures


def _replaced_values(issue: dict[str, Any]) -> list[Any]:
    values = []
    for top in ("fields", "renderedFields"):
        holder = issue.get(top) or {}
        values += [holder.get(name) for name in sf.PROSE_FIELDS]
        comment = holder.get("comment") if isinstance(holder.get("comment"), dict) else {}
        values += [c.get("body") for c in comment.get("comments") or ()]
    values += [c.get("body") for c in (issue.get("fields") or {}).get("comments") or ()]
    for history in (issue.get("changelog") or {}).get("histories") or ():
        for item in history.get("items") or ():
            if item.get("field") in sf.PROSE_ITEM_FIELDS:
                values += [item.get("fromString"), item.get("toString")]
            if item.get("field") == sf.REMOVED_COMMENT_FIELD:
                values += [item.get("from"), item.get("to")]
    return values


def test_committed_fixtures_parse_hold_only_the_placeholder_and_match_the_manifest() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    files = sorted(p for p in FIXTURES.glob("*.json") if p.name != "manifest.json")
    assert [p.stem for p in files] == sorted(manifest["files"])
    file_keys = set()
    for path in files:
        issues = json_util.loads(path.read_text())
        keys = [issue["key"] for issue in issues]
        assert keys == manifest["files"][path.stem]
        for issue in issues:
            assert rules.project_key(issue) == path.stem
            assert all(v is None or v == PLACEHOLDER for v in _replaced_values(issue)), issue["key"]
        file_keys.update(keys)
    drawn = {k for group in manifest["groups"] for k in group["keys"]}
    drawn |= {k for link in manifest["links"]["links"] for k in link["ends"]}
    assert file_keys == drawn == set(manifest["replaced"])
    total = sum(path.stat().st_size for path in files)
    assert total == manifest["total_bytes"] <= rules.FIXTURE_MAX_BYTES
