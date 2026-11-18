"""The words the page adds, pinned to spec 0016 *Page text* (AC-13).

Every string here is copied from spec 0016 (`docs/specs/0016-public-results-page/
index.md`), never from the code, so a change in `orgos.site.text` that departs
from the spec fails here.
"""

from orgos.site import text

SPEC = {
    "K1": "OrgOS · Phase 1 result",
    "K2_author": "Ghaly Nicolas Jules",
    "K2_author_url": "https://github.com/ghalynho10",
    "K2_code": "Code and eval: github.com/ghalynho10/OrgOS",
    "K2_code_url": "https://github.com/ghalynho10/OrgOS",
    "K4": (
        "The frozen rule, by question type, with the smallest p each type could have given",
        "Correct runs and answers right, by question type and store",
        "Answers right with and without the two zero answer questions, <Q03 and Q16>",
        "Graph minus SQL, correct runs of 3, per question",
        "Answers right with and without the reading note",
    ),
    "K5": (
        "Group 1, from the scope",
        "Group 2, method",
        "Group 3, data and scoring",
        "Group 4, agent setup",
        "Group 5, agent version 2 and the plain runs",
    ),
    "K6": "Limitations",
    "K7": (
        "OrgOS is a prototype of a system a company would build for itself. It tests one design"
        " question, a SQL store against a graph store for an AI agent over work data, with a"
        " frozen eval that checks citations. This page makes no claim that a company wants it"
        " and, like the conclusion above, names no winner."
    ),
    "K8": (
        "The design specs behind this work are kept private. Where a limitation names a spec or"
        " a note, it points to a document that is not published here."
    ),
    "K9": (
        "The data is The Public Jira Dataset (Zenodo record 15719919), licensed CC BY 4.0."
        " Montgomery L, Lüders C, Maalej W. An Alternative Issue Tracking Dataset of Public Jira"
        " Repositories. In 2022 IEEE/ACM 19th International Conference on Mining Software"
        " Repositories (MSR) 2022 May 23 (pp. 73-77). IEEE."
    ),
    "K10": (
        "The public code repository also holds test fixtures and a data description built from"
        " that dataset. They contain real looking names and email addresses from the dataset's"
        " change history, which are already public in the dataset. This page repeats none of"
        " them."
    ),
    "K11": "The findings report marks this drop as untested: no test was run on it.",
    "L1": (
        "Type",
        "Questions",
        "Untied",
        "Graph ahead",
        "SQL ahead",
        "p",
        "Smallest p the test could give",
    ),
    "L2": (
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
    ),
    "L3": (
        "Type",
        "Store",
        "With the note",
        "Without the note",
        "With the note, without <Q03 and Q16>",
        "Without the note, without <Q03 and Q16>",
    ),
    "L4": ("Question", "Type", "Graph minus SQL"),
    "L5": ("Scope", "Store", "With the note", "Without the note"),
    "L5_scopes": ("All questions", "path_connection"),
    "L6": ("SQL", "Graph"),
    "L7": "On this page",
    "canonical": "https://ghalynho10.github.io/OrgOS/",
}


def _spec(value: str) -> str:
    """The spec writes a filled value in angle brackets; the code writes `{questions}`."""
    return value.replace("<Q03 and Q16>", "{questions}")


def test_constants_equal_the_spec() -> None:
    assert SPEC["K1"] == text.K1_HEADER
    assert (SPEC["K2_author"], SPEC["K2_author_url"]) == (text.K2_AUTHOR, text.K2_AUTHOR_URL)
    assert (SPEC["K2_code"], SPEC["K2_code_url"]) == (text.K2_CODE, text.K2_CODE_URL)
    assert tuple(_spec(value) for value in SPEC["K4"]) == (
        text.K4_RULE,
        text.K4_COUNTS,
        text.K4_ZERO,
        text.K4_DIFFERENCES,
        text.K4_NOTE,
    )
    assert SPEC["K5"] == text.K5_GROUPS
    assert SPEC["K6"] == text.K6_LIMITATIONS
    assert SPEC["K7"] == text.K7_NOT_CLAIMED
    assert SPEC["K8"] == text.K8_PRIVATE_SPECS
    assert SPEC["K9"] == text.K9_CREDIT
    assert SPEC["K10"] == text.K10_ALREADY_PUBLISHED
    assert SPEC["K11"] == text.K11_UNTESTED
    assert SPEC["L1"] == text.L1_RULE
    assert SPEC["L2"] == text.L2_COUNTS
    assert tuple(_spec(value) for value in SPEC["L3"]) == text.L3_ZERO
    assert SPEC["L4"] == text.L4_DIFFERENCES
    assert SPEC["L5"] == text.L5_NOTE
    assert SPEC["L5_scopes"] == text.L5_SCOPES
    assert tuple(text.L6_STORES.values()) == SPEC["L6"]
    assert SPEC["L7"] == text.L7_CONTENTS
    assert SPEC["canonical"] == text.CANONICAL_URL
    assert text.K1_TITLE_WORD in SPEC["K1"]
