"""Every word the page adds to the conclusion and the report (spec 0016, *Page text*).

The constants K1 to K11 and the labels L1 to L7 are copied from the spec; a test
pins each one to the spec's string (AC-13). Text in angle brackets in the spec is
filled at build time, marked here by `{questions}`. K3 is not here: it is taken
from the conclusion at build time.
"""

from __future__ import annotations

from typing import Final

CANONICAL_URL: Final = "https://ghalynho10.github.io/OrgOS/"

K1_HEADER: Final = "OrgOS · Phase 1 result"
K1_TITLE_WORD: Final = "OrgOS"
K2_AUTHOR: Final = "Ghaly Nicolas Jules"
K2_AUTHOR_URL: Final = "https://github.com/ghalynho10"
K2_CODE: Final = "Code and eval: github.com/ghalynho10/OrgOS"
K2_CODE_URL: Final = "https://github.com/ghalynho10/OrgOS"
K4_RULE: Final = "The frozen rule, by question type, with the smallest p each type could have given"
K4_COUNTS: Final = "Correct runs and answers right, by question type and store"
K4_ZERO: Final = "Answers right with and without the two zero answer questions, {questions}"
K4_DIFFERENCES: Final = "Graph minus SQL, correct runs of 3, per question"
K4_NOTE: Final = "Answers right with and without the reading note"
K5_GROUPS: Final = (
    "Group 1, from the scope",
    "Group 2, method",
    "Group 3, data and scoring",
    "Group 4, agent setup",
    "Group 5, agent version 2 and the plain runs",
)
K6_LIMITATIONS: Final = "Limitations"
K7_NOT_CLAIMED: Final = (
    "OrgOS is a prototype of a system a company would build for itself. It tests one design"
    " question, a SQL store against a graph store for an AI agent over work data, with a frozen"
    " eval that checks citations. This page makes no claim that a company wants it and, like the"
    " conclusion above, names no winner."
)
K8_PRIVATE_SPECS: Final = (
    "The design specs behind this work are kept private. Where a limitation names a spec or a"
    " note, it points to a document that is not published here."
)
K9_CREDIT: Final = (
    "The data is The Public Jira Dataset (Zenodo record 15719919), licensed CC BY 4.0."
    " Montgomery L, Lüders C, Maalej W. An Alternative Issue Tracking Dataset of Public Jira"
    " Repositories. In 2022 IEEE/ACM 19th International Conference on Mining Software"
    " Repositories (MSR) 2022 May 23 (pp. 73-77). IEEE."
)
K10_ALREADY_PUBLISHED: Final = (
    "The public code repository also holds test fixtures and a data description built from that"
    " dataset. They contain real looking names and email addresses from the dataset's change"
    " history, which are already public in the dataset. This page repeats none of them."
)
K11_UNTESTED: Final = "The findings report marks this drop as untested: no test was run on it."

L1_RULE: Final = (
    "Type",
    "Questions",
    "Untied",
    "Graph ahead",
    "SQL ahead",
    "p",
    "Smallest p the test could give",
)
L2_COUNTS: Final = (
    "Type",
    "Store",
    "Correct with the note",
    "Answers right without the note",
    "Runs with a retry",
    "Attempts timed out",
    "Attempts at the row cap",
    "Limit hit: requests",
    "Limit hit: tokens",
    "Limit hit: tool calls",
)
L3_ZERO: Final = (
    "Type",
    "Store",
    "With the note",
    "Without the note",
    "With the note, without {questions}",
    "Without the note, without {questions}",
)
L4_DIFFERENCES: Final = ("Question", "Type", "Graph minus SQL")
L5_NOTE: Final = ("Scope", "Store", "With the note", "Without the note")
L5_SCOPES: Final = ("All questions", "path_connection")
L6_STORES: Final = {"sql": "SQL", "graph": "Graph"}
L7_CONTENTS: Final = "On this page"

# Every constant string, for the numbers check (AC-9) and the wording check (AC-35).
ALL_TEXT: Final = (
    K1_HEADER,
    K2_AUTHOR,
    K2_CODE,
    K4_RULE,
    K4_COUNTS,
    K4_ZERO,
    K4_DIFFERENCES,
    K4_NOTE,
    *K5_GROUPS,
    K6_LIMITATIONS,
    K7_NOT_CLAIMED,
    K8_PRIVATE_SPECS,
    K9_CREDIT,
    K10_ALREADY_PUBLISHED,
    K11_UNTESTED,
    *L1_RULE,
    *L2_COUNTS,
    *L3_ZERO,
    *L4_DIFFERENCES,
    *L5_NOTE,
    *L5_SCOPES,
    *L6_STORES.values(),
    L7_CONTENTS,
)
