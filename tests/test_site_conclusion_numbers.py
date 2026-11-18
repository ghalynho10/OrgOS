"""Each count of the conclusion's sections 1 to 4 that the report holds equals its cell (AC-5).

The counts are read from `site/conclusion.md` by pattern and compared with the
generated tables. Three counts the conclusion states are not held by one report
field and are not checked here (AC-41): `SQL 7 of 8 failed runs, graph 5 of 8`,
`Q09 3/3 against 0/3, both 0/3 without it` and `ahead by 1 question`.
"""

import re

from tests.site_pages import conclusion_text, page, tables

STORE = {"SQL": "SQL", "graph": "Graph"}


def _section(number: int) -> str:
    return re.split(r"(?m)^## ", conclusion_text())[number]


def test_section_1_rule_table_equals_the_generated_rule_table() -> None:
    typed = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in _section(1).splitlines()
        if line.startswith("| ") and not line.startswith("| -")
    ]
    assert typed[0] == ["Type", "Questions", "Untied", "Graph ahead", "SQL ahead", "p"]
    generated = tables(page())["table-rule"]["rows"]
    assert [row[:6] for row in generated] == typed[1:]


def test_section_2_counts_and_parenthesized_values_equal_the_counts_table() -> None:
    rows = {(r[0], r[1]): r for r in tables(page())["table-counts"]["rows"]}
    found = re.findall(
        r"^- (\w+): SQL (\d+/\d+) \((\d+/\d+)\), graph (\d+/\d+) \((\d+/\d+)\)$",
        _section(2),
        re.MULTILINE,
    )
    assert len(found) == 3
    for kind, sql, sql_plain, graph, graph_plain in found:
        assert rows[(kind, "SQL")][2:4] == [sql, sql_plain]
        assert rows[(kind, "Graph")][2:4] == [graph, graph_plain]


def test_section_3_differences_equal_the_differences_table() -> None:
    line = re.search(r"Graph minus SQL, correct runs out of 3: (.*?)\. All other", _section(3))
    assert line is not None
    assert line.group(1) == "Q04 +2, Q08 +1, Q09 \N{MINUS SIGN}3, Q13 +1, Q14 \N{MINUS SIGN}1"
    typed = dict(item.split(" ") for item in line.group(1).split(", "))
    generated = {row[0]: row[2] for row in tables(page())["table-differences"]["rows"]}
    assert {q: d for q, d in generated.items() if d != "0"} == typed
    assert len(generated) == 20


def test_section_4_pairs_equal_the_reading_note_table() -> None:
    section = _section(4)
    rows = {(r[0], r[1]): r[2:] for r in tables(page())["table-note"]["rows"]}
    total = re.search(
        r"from (\d+) to (\d+) of (\d+) \(SQL\) and (\d+) to (\d+) of (\d+) \(graph\)", section
    )
    assert total is not None
    sql_noted, sql_plain, sql_total, graph_noted, graph_plain, graph_total = total.groups()
    assert (sql_noted, sql_plain, graph_noted, graph_plain) == ("59", "47", "57", "44")
    assert rows[("All questions", "SQL")] == [
        f"{sql_noted}/{sql_total}",
        f"{sql_plain}/{sql_total}",
    ]
    assert rows[("All questions", "Graph")] == [
        f"{graph_noted}/{graph_total}",
        f"{graph_plain}/{graph_total}",
    ]
    paths = re.search(r"SQL (\d+) to (\d+), graph (\d+) to (\d+), of (\d+)", section)
    assert paths is not None
    a, b, c, d, of = paths.groups()
    assert (a, b, c, d) == ("20", "11", "18", "10")
    assert rows[("path_connection", "SQL")] == [f"{a}/{of}", f"{b}/{of}"]
    assert rows[("path_connection", "Graph")] == [f"{c}/{of}", f"{d}/{of}"]
