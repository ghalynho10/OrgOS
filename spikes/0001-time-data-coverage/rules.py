"""The pinned rule set of spike 0001 (spec 0004): every threshold and the pure rule functions.

Committed before any measurement runs (AC-1). The scripts refuse to run when
this file differs from HEAD, and tests/test_spike_rules.py pins every number
to the spec's rule table, so a silent edit fails. Change it only through a
dated amendment the spec allows (AC-14 N = 20, or an AC-7a path correction).

Throwaway spike code: pure functions over plain documents, no I/O.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

# Rule 1, fills the time fields (AC-9).
N = 30
X_PERCENT = 20
P = 3

# Rule 2, the estimate field. Both are recorded; only the first selects.
ESTIMATE_FIELD = "timeoriginalestimate"
OTHER_ESTIMATE_FIELD = "timeestimate"

# Rule 3 and 11, bot (AC-8): at least 20 positive entries and
# 10 * top_count >= 9 * positive_count.
BOT_MIN_POSITIVE = 20
BOT_SHARE_NUMERATOR = 9
BOT_SHARE_DENOMINATOR = 10
ROUND_HOUR_SECONDS = 3600

# Rule 4, shares contributors (AC-12).
K = 2

# Rule 9b and 10, the pick and the fallback ladder (AC-13, AC-14).
GROUP_TARGET_MIN = 5
GROUP_MAX = 10
GROUP_SHORT_MIN = 3
K_FALLBACK = 1
N_RELAXED = 20

# Rule 12, person id (AC-11).
PERSON_ID_ACCOUNT_ID = "accountId"
PERSON_ID_KEY = "key"
PERSON_ID_MIN_PERCENT = 50
PERSON_ID_SHARE_FINDING_PERCENT = 95

# Rule 13, the time entry (AC-7).
ENTRY_FIELD = "timespent"
ENTRY_FIELDTYPE = "jira"
WORKLOG_FIELD = "WorklogId"

# Rule 9, the unit: a project key inside one collection.
NONE_PROJECT = "(none)"

# Rule 6, fixtures (AC-16 to AC-18).
SEED = 20261028
QUOTA_TIMED = 100
QUOTA_NO_TIME = 60
QUOTA_FLAGGED = 30
QUOTA_NEGATIVE = 30
QUOTA_LINKS = 40
PLACEHOLDER = "[prose removed for fixtures]"
FIXTURE_MAX_BYTES = 10_000_000

_INTEGER = re.compile(r"-?[0-9]+")


class RuleError(Exception):
    """Raised when a document breaks a rule's assumption; the run stops."""


# Time entries (AC-7)


@dataclass(frozen=True)
class Author:
    """The author of one changelog history entry, as the document gives it."""

    account_id: str | None
    key: str | None
    name: str | None


@dataclass(frozen=True)
class TimeEntry:
    """One logged amount rebuilt from a changelog history entry."""

    issue_key: str
    amount: int
    author: Author
    created: str | None
    worklog_id: str | None
    timespent_items: int


def _filled(value: Any) -> bool:
    return value is not None and value != ""


def _text(value: Any) -> str | None:
    return str(value) if _filled(value) else None


def to_seconds(value: Any, issue_key: str) -> int:
    """A changelog `from`/`to` value as integer seconds; empty is 0, anything else stops."""
    if not _filled(value):
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        return int(value)
    raise RuleError(f"issue {issue_key}: timespent value {value!r} is not an integer")


def _side(item: Mapping[str, Any], raw: str, string: str) -> Any:
    # The raw value first; the String value only when the raw one is null or empty.
    value = item.get(raw)
    return value if _filled(value) else item.get(string)


def entry_from_history(issue_key: str, history: Mapping[str, Any]) -> TimeEntry | None:
    """The time entry a history entry holds, or None when it holds no `timespent` item."""
    items = history.get("items") or ()
    spent = [
        item
        for item in items
        if item.get("field") == ENTRY_FIELD and item.get("fieldtype") == ENTRY_FIELDTYPE
    ]
    if not spent:
        return None
    amount = sum(
        to_seconds(_side(item, "to", "toString"), issue_key)
        - to_seconds(_side(item, "from", "fromString"), issue_key)
        for item in spent
    )
    worklog_id = None
    for item in items:
        if item.get("field") == WORKLOG_FIELD:
            worklog_id = _text(item.get("from")) or _text(item.get("to"))
            break
    author = history.get("author") or {}
    return TimeEntry(
        issue_key=issue_key,
        amount=amount,
        author=Author(
            account_id=_text(author.get("accountId")),
            key=_text(author.get("key")),
            name=_text(author.get("name")),
        ),
        created=_text(history.get("created")),
        worklog_id=worklog_id,
        timespent_items=len(spent),
    )


def entries_from_issue(issue: Mapping[str, Any]) -> tuple[TimeEntry, ...]:
    """Every time entry of one issue document, in changelog order."""
    key = str(issue.get("key"))
    histories = (issue.get("changelog") or {}).get("histories") or ()
    entries = (entry_from_history(key, history) for history in histories)
    return tuple(entry for entry in entries if entry is not None)


def project_key(issue: Mapping[str, Any]) -> str:
    """The issue's current `fields.project.key`, or `(none)`."""
    project = (issue.get("fields") or {}).get("project") or {}
    return _text(project.get("key")) or NONE_PROJECT


def field_number(value: Any) -> int | float:
    """An issue level time field as a number; null or a non number reads as 0 (as Mongo's `$gt`)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    return 0


# Person id (AC-11)


def choose_person_id_field(with_account_id: int, total: int) -> str:
    """`accountId` when at least 50 percent carry one, otherwise `key`."""
    if total > 0 and 100 * with_account_id >= PERSON_ID_MIN_PERCENT * total:
        return PERSON_ID_ACCOUNT_ID
    return PERSON_ID_KEY


def person_id(author: Author, field: str) -> str | None:
    """The author's id under the collection's chosen field, or None when absent."""
    return author.account_id if field == PERSON_ID_ACCOUNT_ID else author.key


def person_share_is_finding(with_field: int, total: int) -> bool:
    """True when under 95 percent of entries carry the chosen field."""
    return 100 * with_field < PERSON_ID_SHARE_FINDING_PERCENT * total


# Bots (AC-8)


@dataclass(frozen=True)
class BotCheck:
    """The bot test of one account over all its entries in one collection."""

    account: str
    entries: int
    positive: int
    top_amount: int | None
    top_count: int
    flagged: bool

    @property
    def round_hour(self) -> bool:
        """True when the top amount is a whole number of hours (a limitation to name)."""
        return self.top_amount is not None and self.top_amount % ROUND_HOUR_SECONDS == 0


def check_account(account: str, amounts: Sequence[int]) -> BotCheck:
    """Apply the bot rule to one account's entry amounts (exact integer seconds)."""
    positives = Counter(amount for amount in amounts if amount > 0)
    positive = sum(positives.values())
    top_amount, top_count = None, 0
    if positives:
        # Most entries first; on a tie the smaller amount is the top.
        top_amount, top_count = min(positives.items(), key=lambda pair: (-pair[1], pair[0]))
    flagged = (
        positive >= BOT_MIN_POSITIVE
        and BOT_SHARE_DENOMINATOR * top_count >= BOT_SHARE_NUMERATOR * positive
    )
    return BotCheck(account, len(amounts), positive, top_amount, top_count, flagged)


def check_accounts(entries: Iterable[TimeEntry], field: str) -> tuple[BotCheck, ...]:
    """The bot test for every account of one collection, sorted by account.

    Entries whose author lacks the person id field belong to no account and
    are never flagged.
    """
    amounts: dict[str, list[int]] = {}
    for entry in entries:
        account = person_id(entry.author, field)
        if account is not None:
            amounts.setdefault(account, []).append(entry.amount)
    return tuple(check_account(account, amounts[account]) for account in sorted(amounts))


def flagged_ids(checks: Iterable[BotCheck], *, lift_round_hour: bool = False) -> frozenset[str]:
    """The flagged accounts; `lift_round_hour` drops round hour flags (diagnostic only)."""
    return frozenset(
        check.account
        for check in checks
        if check.flagged and not (lift_round_hour and check.round_hour)
    )


def is_flagged(entry: TimeEntry, flagged: frozenset[str], field: str) -> bool:
    """True when the entry's author is a flagged account of this collection."""
    account = person_id(entry.author, field)
    return account is not None and account in flagged


def bot_time(entries: Iterable[TimeEntry], flagged: frozenset[str], field: str) -> tuple[int, int]:
    """(positive seconds by flagged accounts, positive seconds by all accounts)."""
    bot, total = 0, 0
    for entry in entries:
        if entry.amount > 0:
            total += entry.amount
            if is_flagged(entry, flagged, field):
                bot += entry.amount
    return bot, total


# Issues and projects (AC-9, AC-10)


@dataclass(frozen=True)
class IssueFacts:
    """What the rules need from one issue document."""

    key: str
    project: str
    timespent: int | float
    original_estimate: int | float
    remaining_estimate: int | float
    entries: tuple[TimeEntry, ...]


def issue_facts(issue: Mapping[str, Any]) -> IssueFacts:
    """Read the rule inputs out of one issue document."""
    fields = issue.get("fields") or {}
    return IssueFacts(
        key=str(issue.get("key")),
        project=project_key(issue),
        timespent=field_number(fields.get(ENTRY_FIELD)),
        original_estimate=field_number(fields.get(ESTIMATE_FIELD)),
        remaining_estimate=field_number(fields.get(OTHER_ESTIMATE_FIELD)),
        entries=entries_from_issue(issue),
    )


def net_seconds(issue: IssueFacts, flagged: frozenset[str], field: str) -> int:
    """Logged seconds after flagged entries are excluded; entries with no person id stay."""
    return sum(e.amount for e in issue.entries if not is_flagged(e, flagged, field))


def match_status(
    issue: IssueFacts, flagged: frozenset[str], field: str
) -> Literal["match", "mismatch"] | None:
    """Whether the entries sum to the issue's final `timespent` (AC-10).

    None when the issue is outside the check: it has a flagged entry, or it
    has neither an entry nor a `timespent` above 0.
    """
    if any(is_flagged(e, flagged, field) for e in issue.entries):
        return None
    if not issue.entries and not issue.timespent > 0:
        return None
    total = sum(e.amount for e in issue.entries)
    return "match" if total == issue.timespent else "mismatch"


@dataclass(frozen=True)
class ProjectMeasure:
    """The fills rule inputs of one project in one collection."""

    project: str
    stage1_total: int
    qualifying: int
    qualifying_remaining: int
    people: frozenset[str]


def measure_project(
    project: str,
    stage1_total: int,
    issues: Iterable[IssueFacts],
    flagged: frozenset[str],
    field: str,
) -> ProjectMeasure:
    """Count qualifying issues (both estimate fields) and contributors of one project."""
    qualifying, qualifying_remaining = 0, 0
    people: set[str] = set()
    for issue in issues:
        net = net_seconds(issue, flagged, field)
        qualifying += net > 0 and issue.original_estimate > 0
        qualifying_remaining += net > 0 and issue.remaining_estimate > 0
        for entry in issue.entries:
            account = person_id(entry.author, field)
            if entry.amount > 0 and account is not None and account not in flagged:
                people.add(account)
    return ProjectMeasure(
        project, stage1_total, qualifying, qualifying_remaining, frozenset(people)
    )


# Rule sets and the ladder (AC-9, AC-14)


@dataclass(frozen=True)
class RuleSet:
    """The thresholds in force at one ladder step; `x_percent` None means X is dropped."""

    step: int
    n: int
    x_percent: int | None
    p: int
    k: int


def rule_set(step: int, *, relaxed: bool = False) -> RuleSet:
    """Step 0 is the original rules; 1 drops X; 2 and 3 also use K = 1 (steps are cumulative)."""
    if step not in (0, 1, 2, 3):
        raise RuleError(f"no ladder step {step}")
    n = N_RELAXED if relaxed else N
    x_percent = X_PERCENT if step == 0 else None
    k = K if step < 2 else K_FALLBACK
    return RuleSet(step, n, x_percent, P, k)


Verdict = Literal["accept", "next", "accept_short", "stop", "no_corpus"]


def ladder_verdict(step: int, best_group: int, *, relaxed: bool = False) -> Verdict:
    """What the ladder does with the best group size found at a step.

    Steps 0 and 1 accept 5 or more, else go on. Step 2 accepts 5 or more;
    3 or 4 is step 3 (accept short); below 3 is step 4: `stop` for the
    N = 20 amendment, or `no_corpus` when that rerun is already in force.
    """
    if best_group >= GROUP_TARGET_MIN:
        return "accept"
    if step < 2:
        return "next"
    if best_group >= GROUP_SHORT_MIN:
        return "accept_short"
    return "no_corpus" if relaxed else "stop"


def stage1_survives(project: str, spent_and_original: int, n: int) -> bool:
    """Stage 1: keep a project with at least N issues with both fields above 0."""
    return project != NONE_PROJECT and spent_and_original >= n


def failing_parts(measure: ProjectMeasure, rules: RuleSet) -> tuple[str, ...]:
    """The parts of the fills rule a project fails (empty means it passes)."""
    if measure.project == NONE_PROJECT:
        return ("not a project",)
    parts = []
    if measure.qualifying < rules.n:
        parts.append("N")
    if rules.x_percent is not None and 100 * measure.qualifying < rules.x_percent * (
        measure.stage1_total
    ):
        parts.append("X")
    if len(measure.people) < rules.p:
        parts.append("P")
    return tuple(parts)


# Linking, ranking and the pick (AC-12, AC-13)


def linked(a: frozenset[str], b: frozenset[str], k: int) -> bool:
    """Two projects are linked when at least K people contributed to both."""
    return len(a & b) >= k


def groups(people: Mapping[str, frozenset[str]], k: int) -> tuple[tuple[str, ...], ...]:
    """Connected groups of the given (passing) projects, each sorted, in sorted order."""
    projects = sorted(people)
    seen: set[str] = set()
    found = []
    for start in projects:
        if start in seen:
            continue
        group, todo = {start}, [start]
        while todo:
            current = todo.pop()
            for other in projects:
                if other not in group and linked(people[current], people[other], k):
                    group.add(other)
                    todo.append(other)
        seen |= group
        found.append(tuple(sorted(group)))
    return tuple(sorted(found))


def largest_group(
    found: Iterable[tuple[str, ...]], qualifying: Mapping[str, int]
) -> tuple[str, ...]:
    """The largest group; ties go to more qualifying issues, then the smallest key held."""
    ordered = sorted(
        found, key=lambda group: (-len(group), -sum(qualifying[p] for p in group), min(group))
    )
    return ordered[0] if ordered else ()


@dataclass(frozen=True)
class CollectionScore:
    """One collection's best group at one ladder step."""

    collection: str
    group: tuple[str, ...]
    qualifying: int

    @property
    def size(self) -> int:
        """How many projects the group holds."""
        return len(self.group)


def rank_collections(scores: Iterable[CollectionScore]) -> tuple[CollectionScore, ...]:
    """Collections by group size, then group qualifying issues, then name (code point order)."""
    return tuple(sorted(scores, key=lambda s: (-s.size, -s.qualifying, s.collection)))


def greedy_pick(
    group: Sequence[str],
    qualifying: Mapping[str, int],
    people: Mapping[str, frozenset[str]],
    k: int,
    cap: int = GROUP_MAX,
) -> tuple[str, ...]:
    """Pick inside one group: most qualifying first, then the linked project with the most."""

    def best(candidates: Iterable[str]) -> str | None:
        ordered = sorted(candidates, key=lambda p: (-qualifying[p], p))
        return ordered[0] if ordered else None

    first = best(group)
    if first is None:
        return ()
    chosen = [first]
    while len(chosen) < cap:
        nxt = best(
            p
            for p in group
            if p not in chosen and any(linked(people[p], people[c], k) for c in chosen)
        )
        if nxt is None:
            break
        chosen.append(nxt)
    return tuple(chosen)
