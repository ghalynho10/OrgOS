"""Fixtures (AC-16 to AC-19): draw about 300 corpus issues and strip their prose.

Reads `results/corpus.json` and the Stage 2 record, classifies every issue of
the chosen projects through the reader login, draws the five groups with one
`random.Random(SEED)`, fetches the drawn documents and writes them as
canonical extended JSON under `tests/fixtures/corpus/`. Above
`FIXTURE_MAX_BYTES` it says so and exits non zero: the engineer decides before
any commit. The draw and the prose replacement are pure functions, tested in
`tests/test_spike_fixtures.py`. Throwaway spike code.

    uv run --env-file .env python spikes/0001-time-data-coverage/select_fixtures.py
"""

from __future__ import annotations

import copy
import json
import random
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import common
from bson import json_util
from bson.json_util import CANONICAL_JSON_OPTIONS

rules = common.load_rules()

FIXTURES = common.REPO / "tests" / "fixtures" / "corpus"

GROUPS = (
    ("timed", rules.QUOTA_TIMED),
    ("no_time", rules.QUOTA_NO_TIME),
    ("flagged_entry", rules.QUOTA_FLAGGED),
    ("negative_entry", rules.QUOTA_NEGATIVE),
)

# The AC-17 paths, as written in the manifest and the fixtures README.
PROSE_FIELDS = ("description", "environment")
PROSE_ITEM_FIELDS = ("description", "comment", "environment")
REMOVED_COMMENT_FIELD = "Comment"  # AC-17 amendment 2: the old text sits in the raw from
REPLACED_PATHS = (
    "fields.description",
    "fields.environment",
    "fields.comment.comments[].body",
    "fields.comments[].body (AC-17 amendment: where the Jira collection keeps comments)",
    "renderedFields.description",
    "renderedFields.environment",
    "renderedFields.comment.comments[].body",
    "changelog.histories[].items[].fromString (field description, comment or environment)",
    "changelog.histories[].items[].toString (field description, comment or environment)",
    "changelog.histories[].items[].from (field Comment, AC-17 amendment 2)",
    "changelog.histories[].items[].to (field Comment, AC-17 amendment 2)",
)


@dataclass(frozen=True)
class Candidate:
    """One corpus issue and the groups it can be drawn into."""

    key: str
    id: int
    project: str
    groups: frozenset[str]
    links: tuple[tuple[int, str], ...]  # (link id, other end's key)


@dataclass(frozen=True)
class Draw:
    """The drawn keys per group, the drawn links, and every shortfall."""

    groups: tuple[tuple[str, tuple[str, ...]], ...]
    links: tuple[tuple[int, str, str], ...]  # (link id, smaller key, larger key)
    shortfalls: tuple[tuple[str, int, int], ...]  # (group, quota, drawn)

    @property
    def keys(self) -> tuple[str, ...]:
        """Every drawn issue key, each once, in draw order."""
        seen: dict[str, None] = {}
        for _, keys in self.groups:
            seen.update(dict.fromkeys(keys))
        for _, a, b in self.links:
            seen.update(dict.fromkeys((a, b)))
        return tuple(seen)


def _links(issue: Mapping[str, Any]) -> tuple[tuple[int, str], ...]:
    # Only fields.issuelinks[]; parent and subtasks are not links (AC-16).
    out = []
    for link in (issue.get("fields") or {}).get("issuelinks") or ():
        other = link.get("inwardIssue") or link.get("outwardIssue") or {}
        if link.get("id") is not None and other.get("key"):
            out.append((int(link["id"]), str(other["key"])))
    return tuple(out)


def _no_time(issue: Mapping[str, Any], facts: Any) -> bool:
    spent = (issue.get("fields") or {}).get(rules.ENTRY_FIELD)
    zero = spent is None or (
        isinstance(spent, int | float) and not isinstance(spent, bool) and spent == 0
    )
    return not facts.entries and zero


def classify(issue: Mapping[str, Any], flagged: frozenset[str], field: str) -> Candidate:
    """Which of the four drawn groups one issue document belongs to."""
    facts = rules.issue_facts(issue)
    groups = set()
    if rules.net_seconds(facts, flagged, field) > 0 and facts.original_estimate > 0:
        groups.add("timed")
    if _no_time(issue, facts):
        groups.add("no_time")
    if any(rules.is_flagged(e, flagged, field) for e in facts.entries):
        groups.add("flagged_entry")
    if any(e.amount < 0 for e in facts.entries):
        groups.add("negative_entry")
    return Candidate(facts.key, int(issue["id"]), facts.project, frozenset(groups), _links(issue))


def round_robin(
    per_project: Mapping[str, Sequence[str]], quota: int, drawn: Iterable[str]
) -> tuple[str, ...]:
    """Take one key per project in alphabetical order until the quota, skipping drawn keys."""
    taken: list[str] = []
    skip = set(drawn)
    queues = {p: [k for k in per_project[p] if k not in skip] for p in sorted(per_project)}
    while len(taken) < quota and any(queues.values()):
        for project in sorted(queues):
            if len(taken) == quota:
                break
            if queues[project]:
                taken.append(queues[project].pop(0))
    return tuple(taken)


def corpus_links(candidates: Iterable[Candidate]) -> tuple[tuple[int, str, str], ...]:
    """Every link with both ends in the corpus, once per link id, sorted by id."""
    found = tuple(candidates)
    keys = {c.key for c in found}
    links: dict[int, tuple[int, str, str]] = {}
    for c in found:
        for link_id, other in c.links:
            if other in keys:
                a, b = sorted((c.key, other))
                links.setdefault(link_id, (link_id, a, b))
    return tuple(links[i] for i in sorted(links))


def draw(candidates: Iterable[Candidate], seed: int = rules.SEED) -> Draw:
    """The AC-16 draw: four round robin groups, then one global link shuffle, one Random."""
    found = sorted(candidates, key=lambda c: c.id)
    projects = sorted({c.project for c in found})
    rng = random.Random(seed)
    drawn: list[str] = []
    groups, shortfalls = [], []
    for group, quota in GROUPS:
        per_project: dict[str, list[str]] = {p: [] for p in projects}
        for c in found:
            if group in c.groups:
                per_project[c.project].append(c.key)
        for project in projects:
            rng.shuffle(per_project[project])
        taken = round_robin(per_project, quota, drawn)
        drawn.extend(taken)
        groups.append((group, taken))
        if len(taken) < quota:
            shortfalls.append((group, quota, len(taken)))
    links = list(corpus_links(found))
    rng.shuffle(links)
    chosen = tuple(links[: rules.QUOTA_LINKS])
    if len(chosen) < rules.QUOTA_LINKS:
        shortfalls.append(("links", rules.QUOTA_LINKS, len(chosen)))
    return Draw(tuple(groups), chosen, tuple(shortfalls))


def _replace(holder: Any, name: str, path: str, paths: list[str]) -> None:
    if isinstance(holder, dict) and holder.get(name) is not None:
        holder[name] = rules.PLACEHOLDER
        paths.append(path)


def strip_prose(issue: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """A copy of the issue with the AC-17 prose replaced, and the concrete paths replaced."""
    out = copy.deepcopy(dict(issue))
    paths: list[str] = []
    for top in ("fields", "renderedFields"):
        holder = out.get(top)
        if not isinstance(holder, dict):
            continue
        for name in PROSE_FIELDS:
            _replace(holder, name, f"{top}.{name}", paths)
        comment_holder = holder.get("comment")
        comments = comment_holder.get("comments") if isinstance(comment_holder, dict) else None
        for i, comment in enumerate(comments or ()):
            _replace(comment, "body", f"{top}.comment.comments[{i}].body", paths)
    # AC-17 amendment: the Jira collection keeps comments as a list at fields.comments.
    listed = (out.get("fields") or {}).get("comments")
    for i, comment in enumerate(listed if isinstance(listed, list) else ()):
        _replace(comment, "body", f"fields.comments[{i}].body", paths)
    histories = (out.get("changelog") or {}).get("histories") or ()
    for h, history in enumerate(histories):
        for i, item in enumerate((history or {}).get("items") or ()):
            if not isinstance(item, dict):
                continue
            if item.get("field") in PROSE_ITEM_FIELDS:
                sides: tuple[str, ...] = ("fromString", "toString")
            elif item.get("field") == REMOVED_COMMENT_FIELD:
                sides = ("from", "to")
            else:
                continue
            for side in sides:
                _replace(item, side, f"changelog.histories[{h}].items[{i}].{side}", paths)
    return out, tuple(paths)


def to_file_text(docs: Sequence[Mapping[str, Any]]) -> str:
    """One project's fixture file: a JSON array, one canonical extended JSON issue per line."""
    lines = [json_util.dumps(doc, json_options=CANONICAL_JSON_OPTIONS) for doc in docs]
    return "[\n" + ",\n".join(lines) + "\n]\n"


def _pipeline(projects: Sequence[str]) -> list[dict[str, Any]]:
    import stage2_entries

    stages = stage2_entries.pipeline()
    stages[0]["$project"].update(
        {
            "id": 1,
            "fields.issuelinks.id": 1,
            "fields.issuelinks.inwardIssue.key": 1,
            "fields.issuelinks.outwardIssue.key": 1,
        }
    )
    return [{"$match": {"fields.project.key": {"$in": list(projects)}}}, *stages]


def main() -> None:
    """Draw, fetch, strip and write the fixtures, the manifest, and the record section."""
    record = common.guard()
    corpus = json.loads((common.RESULTS / "corpus.json").read_text())
    collection = corpus["collection"]
    projects = [p["key"] for p in corpus["projects"]]
    stage2 = json.loads((common.RESULTS / "run_record.json").read_text())["stage2"]
    field = stage2["per_collection"][collection]["person_id_field"]
    flagged = frozenset(
        row["account"]
        for row in common.read_csv("flagged_accounts.csv")
        if row["collection"] == collection
    )

    db = common.reader()
    candidates = []
    for doc in db[collection].aggregate(_pipeline(projects), allowDiskUse=True, batchSize=2000):
        doc["changelog"] = {"histories": doc.pop("histories")}
        candidates.append(classify(doc, flagged, field))
    picked = draw(candidates)
    keys = picked.keys
    print(f"{collection}: {len(candidates)} corpus issues, {len(keys)} drawn", flush=True)

    docs = {doc["key"]: doc for doc in db[collection].find({"key": {"$in": list(keys)}})}
    missing = sorted(set(keys) - set(docs))
    if missing:
        sys.exit(f"drawn issues not found: {missing}; stopping")
    ids = {c.key: c.id for c in candidates}
    per_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    replaced: dict[str, tuple[str, ...]] = {}
    for key in sorted(keys, key=lambda k: ids[k]):
        stripped, paths = strip_prose(docs[key])
        per_file[rules.project_key(stripped)].append(stripped)
        replaced[key] = paths

    texts = {project: to_file_text(per_file[project]) for project in sorted(per_file)}
    total = sum(len(text.encode()) for text in texts.values())
    print(f"total size {total} bytes (limit {rules.FIXTURE_MAX_BYTES})", flush=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for project, text in texts.items():
        (FIXTURES / f"{project}.json").write_text(text)
    group_rows = dict(picked.groups)
    manifest = {
        "dataset": "The Public Jira Dataset, Zenodo record 15719919 (2025-06-23), CC BY 4.0",
        "collection": collection,
        "seed": rules.SEED,
        "ladder_step": corpus["ladder_step"],
        "projects": projects,
        "groups": [
            {
                "group": group,
                "quota": quota,
                "drawn": len(group_rows[group]),
                "candidates": sum(group in c.groups for c in candidates),
                "keys": list(group_rows[group]),
            }
            for group, quota in GROUPS
        ],
        "links": {
            "quota": rules.QUOTA_LINKS,
            "drawn": len(picked.links),
            "candidates": len(corpus_links(candidates)),
            "links": [{"id": i, "ends": [a, b]} for i, a, b in picked.links],
        },
        "shortfalls": [
            {"group": group, "quota": quota, "drawn": drawn}
            for group, quota, drawn in picked.shortfalls
        ],
        "files": {
            project: [doc["key"] for doc in per_file[project]] for project in sorted(per_file)
        },
        "total_bytes": total,
        "replaced_paths": list(REPLACED_PATHS),
        "placeholder": rules.PLACEHOLDER,
        "replaced": {key: list(replaced[key]) for key in sorted(replaced, key=lambda k: ids[k])},
    }
    (FIXTURES / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    common.update_record(
        "fixtures",
        {
            **record,
            "issues": len(keys),
            "total_bytes": total,
            "shortfalls": manifest["shortfalls"],
        },
    )
    if total > rules.FIXTURE_MAX_BYTES:
        sys.exit(f"fixtures total {total} bytes, over {rules.FIXTURE_MAX_BYTES}: do not commit")


if __name__ == "__main__":
    main()
