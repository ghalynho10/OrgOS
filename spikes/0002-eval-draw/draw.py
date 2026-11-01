"""Seeded draw of the placeholders in the eval questions (spec 0005, step 1).

Fills each question's [project], [issue X] and [date range] from allowed sources only:
the committed fixture sample and the spike 0001 people list. SEED and DRAWS are
committed and pushed before the first run; one run, no redraws. Per question the order
is project, then issue, then date range, each inside the question's projects.
"""

import csv
import hashlib
import json
import random
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from bson import json_util

SEED = 20261030
# question id -> rule. projects: the question's projects; draw: how many of them to draw
# (omit for none); issue: link_issue | timed_issue | parent_issue (has sub-tasks) | person
# (optional); date: created | resolved | entry (optional), the field the date range applies to.
# distinct: True keeps the issue different from issues already drawn earlier in this run.
DRAWS: dict[str, dict] = {
    "Q01": {"projects": ["BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER"], "date": "created"},
    "Q02": {"projects": ["BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER"], "draw": 1},
    "Q03": {"projects": ["BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER"], "draw": 1, "date": "resolved"},
    "Q06": {"projects": ["BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER"], "date": "entry"},
    "Q13": {"projects": ["CLOUD", "CLOV"], "issue": "parent_issue", "distinct": True},
    "Q14": {"projects": ["CLOUD", "CLOV"], "issue": "parent_issue", "distinct": True},
    "Q15": {"projects": ["CLOUD", "CLOV"], "issue": "parent_issue", "distinct": True},
    "Q20": {"projects": ["BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER"], "date": "entry"},
}
FIXTURES = Path("tests/fixtures/corpus")
MANIFEST = FIXTURES / "manifest.json"
PEOPLE = Path("spikes/0001-time-data-coverage/results/project_people.csv")
FLAGGED = Path("spikes/0001-time-data-coverage/results/flagged_accounts.csv")
OUT = Path("spikes/0002-eval-draw/results/draw.json")
CORPUS = ("BAM", "CLOUD", "CLOV", "CRUC", "CWD", "FE", "JRASERVER")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_issues() -> list[dict]:
    docs = []
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == "manifest.json":
            continue
        loaded = json_util.loads(path.read_text())
        docs.extend(loaded if isinstance(loaded, list) else loaded.get("issues", loaded))
    return docs


def utc_day(value) -> date:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def dates_of(doc: dict, field: str) -> list:
    if field == "created":
        return [doc["fields"]["created"]]
    if field == "resolved":
        return [doc["fields"]["resolutiondate"]] if doc["fields"].get("resolutiondate") else []
    return [h["created"] for h in doc.get("changelog", {}).get("histories", [])
            if any(i.get("field") == "timespent" and i.get("fieldtype") == "jira" for i in h.get("items", []))]


def span(issues: list[dict], field: str, projects: list[str]) -> tuple[date, date]:
    starts, ends = [], []
    for project in projects:
        days = [utc_day(v) for d in issues if d["fields"]["project"]["key"] == project for v in dates_of(d, field)]
        if not days:
            sys.exit(f"no {field} dates in {project}")
        starts.append(min(days))
        ends.append(max(days))
    start, end = max(starts), min(ends) + timedelta(days=1)
    if start >= end:
        sys.exit(f"no {field} overlap across {projects}")
    return start, end


def add_months(day: date, n: int) -> date:
    years, month = divmod(day.month - 1 + n, 12)
    return date(day.year + years, month + 1, 1)


def date_range(rng: random.Random, start: date, end: date) -> tuple[date, date, str]:
    for months, unit in ((3, "quarter"), (1, "month")):
        units, begin = [], date(start.year, 1, 1)
        while begin < end:
            stop = add_months(begin, months)
            if begin >= start and stop <= end:
                units.append((begin, stop))
            begin = stop
        if units:
            begin, stop = rng.choice(units)
            return begin, stop, unit
    return start, end, "span"


def issue_pool(kind: str, projects: list[str], issues: list[dict], manifest: dict) -> list[str]:
    project_of = {d["key"]: d["fields"]["project"]["key"] for d in issues}
    if kind == "person":
        bots = {row["account"] for row in csv.DictReader(FLAGGED.open())}
        return sorted({row["person"] for row in csv.DictReader(PEOPLE.open())
                       if row["collection"] == "Jira" and row["project"] in projects and row["person"] not in bots})
    if kind == "link_issue":
        keys = {end for link in manifest["links"]["links"] for end in link["ends"]}
    elif kind == "timed_issue":
        keys = {k for g in manifest["groups"] if g["group"] == "timed" for k in g["keys"]}
    elif kind == "parent_issue":
        keys = {d["key"] for d in issues if d["fields"].get("subtasks")}
    else:
        sys.exit(f"unknown issue pool {kind}")
    return sorted(k for k in keys if project_of.get(k) in projects)


def main() -> None:
    issues = fixture_issues()
    manifest = json.loads(MANIFEST.read_text())
    record = {"seed": SEED, "inputs": {str(p): sha256(p) for p in (MANIFEST, PEOPLE, FLAGGED)}, "draws": {}}
    drawn_issues: set[str] = set()
    for qid, rule in sorted(DRAWS.items()):
        if not set(rule["projects"]) <= set(CORPUS):
            sys.exit(f"{qid}: project outside the corpus")
        rng = random.Random(f"{SEED}:{qid}")
        pool = sorted(rule["projects"])
        scope = sorted(rng.sample(pool, rule["draw"])) if rule.get("draw") else pool
        result = {"project_pool": pool, "projects": scope}
        if rule.get("issue"):
            candidates = [c for c in issue_pool(rule["issue"], scope, issues, manifest)
                          if not (rule.get("distinct") and c in drawn_issues)]
            if not candidates:
                sys.exit(f"{qid}: empty {rule['issue']} pool in {scope}")
            result |= {"issue_pool": rule["issue"], "issue_pool_size": len(candidates), "issue": rng.choice(candidates)}
            drawn_issues.add(result["issue"])
        if rule.get("date"):
            begin, stop, unit = date_range(rng, *span(issues, rule["date"], scope))
            result |= {"date_field": rule["date"], "date_unit": unit, "date_range": f"[{begin}, {stop})"}
        record["draws"][qid] = result
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record["draws"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
