"""The findings report pinned to spec 0014: the rule, its wordings and the reach table.

Every value written here is copied from spec 0014 (`docs/specs/0014-findings-report/
index.md`, the rule frozen by the docs tag `rule-frozen`), never from the code, so a
change to the module that departs from the spec fails here (AC-9).
"""

import itertools
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path

import pytest

from orgos.cli import app
from orgos.eval import report
from orgos.eval.report import (
    ALPHA,
    REACH_TABLE,
    Verdict,
    reach_row,
    rule_lines,
    sign_flip_p,
    smallest_p,
    verdict_line,
)

REPO = Path(__file__).resolve().parents[1]

# Spec 0014, "The reach table", one row per m from 1 to 20.
SPEC_REACH = (
    (1, Fraction(1), None, None),
    (2, Fraction(1, 2), None, None),
    (3, Fraction(1, 4), None, None),
    (4, Fraction(1, 8), None, None),
    (5, Fraction(1, 16), None, None),
    (6, Fraction(1, 32), 0, Fraction(1, 32)),
    (7, Fraction(1, 64), 0, Fraction(1, 64)),
    (8, Fraction(1, 128), 0, Fraction(1, 128)),
    (9, Fraction(1, 256), 1, Fraction(5, 128)),
    (10, Fraction(1, 512), 1, Fraction(11, 512)),
    (11, Fraction(1, 1024), 1, Fraction(3, 256)),
    (12, Fraction(1, 2048), 2, Fraction(79, 2048)),
    (13, Fraction(1, 4096), 2, Fraction(23, 1024)),
    (14, Fraction(1, 8192), 2, Fraction(53, 4096)),
    (15, Fraction(1, 16384), 3, Fraction(9, 256)),
    (16, Fraction(1, 32768), 3, Fraction(697, 32768)),
    (17, Fraction(1, 65536), 4, Fraction(1607, 32768)),
    (18, Fraction(1, 131072), 4, Fraction(253, 8192)),
    (19, Fraction(1, 262144), 4, Fraction(1259, 65536)),
    (20, Fraction(1, 524288), 5, Fraction(5425, 131072)),
)
# Spec 0014 AC-3, AC-6, AC-7, AC-8, AC-5 and AC-10, word for word.
SPEC_NOT_DETECTED = (
    "graph scored higher on {k} of {n} questions; the test did not detect a gap (p = {p});"
    " this does not mean the stores are equal."
)
SPEC_WORDINGS = {
    "NO_RELIABLE": "no reliable difference at this sample size",
    "PARTIAL": "partial, no verdict",
    "COULD_NOT_REACH": "could not reach 0.05 at this m",
    "NO_TEST": "no questions, no test",
    "SECONDARY": "secondary, descriptive",
    "CONTEXT": "context",
    "FAMILY_BOUND": "at most 3 x 0.05 = 0.15",
    "CHANCE_ALL": "at most {t} x 0.05",
}
# Spec 0014 AC-4: today aggregate 7, path and connection 7, mixed 6.
SPEC_PER_TYPE = "questions per type: aggregate 7, path_connection 7, mixed 6"


def brute_force_p(differences: Sequence[int]) -> Fraction:
    """Every sign pattern listed one by one, as the spec defines `p`."""
    observed = abs(sum(differences))
    patterns = list(itertools.product((1, -1), repeat=len(differences)))
    hits = sum(
        1
        for signs in patterns
        if abs(sum(s * d for s, d in zip(signs, differences, strict=True))) >= observed
    )
    return Fraction(hits, len(patterns))


# The rule (AC-1, AC-2)


@pytest.mark.parametrize(
    "differences",
    [(), (0,), (1,), (3, -1), (2, 0, -1, 3), (1, 1, 1, -2, 0, 3), (3, 3, -3, 1, 2, -1, 0)],
)
def test_the_p_equals_a_brute_force_count_of_every_sign_pattern(
    differences: tuple[int, ...],
) -> None:
    assert sign_flip_p(differences) == brute_force_p(differences)


def test_zero_differences_stay_in_the_count_and_do_not_change_p() -> None:
    assert sign_flip_p((2, -1, 3)) == sign_flip_p((2, 0, -1, 0, 3, 0))


def test_all_in_one_direction_gives_one_sixteenth_at_five_and_one_thirty_second_at_six() -> None:
    assert sign_flip_p((1,) * 5) == Fraction(1, 16)
    assert sign_flip_p((-2,) * 6) == Fraction(1, 32)


def test_the_p_is_an_exact_fraction() -> None:
    assert isinstance(sign_flip_p((1, 2, 3)), Fraction)


def test_alpha_is_one_twentieth_and_the_threshold_is_inclusive() -> None:
    assert Fraction(1, 20) == ALPHA
    assert report.is_real(Fraction(1, 20))
    assert not report.is_real(Fraction(1, 20) + Fraction(1, 10**9))


def test_a_real_gap_names_the_store_ahead_by_the_sign_of_the_sum() -> None:
    graph = Verdict("aggregate", "primary", (1, 2, 1, 1, 3, 1))
    sql = Verdict("aggregate", "primary", (-1, -2, -1, -1, -3, -1))
    assert graph.real and graph.direction == "graph"
    assert sql.real and sql.direction == "sql"
    assert verdict_line(graph).endswith("real, graph ahead")
    assert verdict_line(sql).endswith("real, SQL ahead")


# The wordings (AC-3, AC-5, AC-6, AC-9)


def test_the_wordings_are_the_spec_texts() -> None:
    assert SPEC_NOT_DETECTED == report.NOT_DETECTED
    for name, text in SPEC_WORDINGS.items():
        assert getattr(report, name) == text, name
    assert report.MIN_UNTIED == 6
    assert report.NEED_UNTIED == "a test needs at least 6 untied questions to ever read real"


def test_a_primary_test_that_is_not_real_prints_the_fixed_sentence() -> None:
    verdict = Verdict("mixed", "primary", (1, 0, -1, 2, 0, 1))
    line = verdict_line(verdict)
    assert line.endswith(
        "graph scored higher on 3 of 6 questions; the test did not detect a gap"
        f" (p = {verdict.p}); this does not mean the stores are equal."
    )
    assert "real," not in line


def test_a_secondary_test_that_is_not_real_says_no_reliable_difference() -> None:
    line = verdict_line(Verdict("mixed, second form", "secondary", (1, -1, 0, 0, 1)))
    assert line.startswith("secondary, descriptive, mixed, second form: n 5, m 3,")
    assert line.endswith("no reliable difference at this sample size")


def test_no_line_says_equal_no_difference_or_same_except_the_fixed_sentence() -> None:
    cases = [
        Verdict(name, role, differences, partial)
        for name in ("aggregate",)
        for role in ("primary", "secondary", "context")
        for differences in ((), (0, 0), (1, -1), (1,) * 6, (-1,) * 7, (2, 0, -1))
        for partial in (False, True)
    ]
    for verdict in cases:
        line = verdict_line(verdict).replace(report.NOT_DETECTED.split("; ")[-1], "")
        for word in ("equal", "no difference", "same"):
            assert word not in line, (verdict, line)


def test_every_verdict_line_states_n_m_the_signs_p_and_the_reach() -> None:
    line = verdict_line(Verdict("aggregate", "primary", (2, 0, -1, 1, 0, 1, 3)))
    assert line.startswith(
        "primary, aggregate: n 7, m 5, graph ahead 4, SQL ahead 1, tied 2,"
        f" p = {sign_flip_p((2, 0, -1, 1, 0, 1, 3))}, smallest p 1/16;"
        " could not reach 0.05 at this m; direction graph."
    )


def test_the_direction_is_none_when_the_sum_or_m_is_zero() -> None:
    assert Verdict("a", "primary", (1, -1, 0)).direction == "none"
    assert Verdict("a", "primary", (0, 0)).direction == "none"
    assert "direction none" in verdict_line(Verdict("a", "primary", (2, -1, -1)))


def test_the_reach_note_is_left_out_once_the_smallest_p_can_reach_alpha() -> None:
    assert report.COULD_NOT_REACH not in verdict_line(Verdict("a", "primary", (1,) * 6))
    assert report.COULD_NOT_REACH in verdict_line(Verdict("a", "primary", (1,) * 5))


def test_a_type_with_no_questions_prints_no_test() -> None:
    assert verdict_line(Verdict("mixed", "primary", ())) == "primary, mixed: no questions, no test"


def test_the_smallest_p_is_two_over_two_to_the_m_and_one_at_zero() -> None:
    assert smallest_p(0) == 1
    assert smallest_p(1) == 1
    assert smallest_p(6) == Fraction(1, 32)


def test_a_partial_test_prints_partial_no_verdict_and_never_real() -> None:
    verdict = Verdict("aggregate", "primary", (1,) * 7, partial=True)
    line = verdict_line(verdict)
    assert not verdict.real
    assert line.endswith("partial, no verdict")
    assert "real," not in line


def test_the_context_test_prints_the_differences_and_no_verdict_word() -> None:
    verdict = Verdict("all questions", "context", (1,) * 20)
    line = verdict_line(verdict)
    assert not verdict.real
    assert line.startswith("context, all questions: n 20, m 20, graph ahead 20, SQL ahead 0")
    assert line.endswith(f"differences {[1] * 20}")
    for word in ("real", "did not detect", "no reliable"):
        assert word not in line


# The reach table and `--rule` (AC-9, AC-10)


def test_the_reach_table_constant_is_the_spec_table() -> None:
    assert SPEC_REACH == REACH_TABLE


def test_each_reach_row_computed_by_sign_flip_p_equals_the_spec_row() -> None:
    assert tuple(reach_row(m) for m in range(1, 21)) == SPEC_REACH


def test_the_rule_lines_hold_every_reach_row_the_types_and_the_untied_rule() -> None:
    lines = rule_lines({"aggregate": 7, "path_connection": 7, "mixed": 6})
    assert "family bound: at most 3 x 0.05 = 0.15" in lines
    assert "  m 9: smallest p 1/256; most against the leader, equal gaps: 1; p there 5/128" in lines
    assert (
        "  m 5: smallest p 1/16; most against the leader, equal gaps: cannot read real;"
        " p there none"
    ) in lines
    assert sum(1 for line in lines if line.startswith("  m ")) == 20
    assert SPEC_PER_TYPE in lines
    assert lines[-1] == "a test needs at least 6 untied questions to ever read real"


def test_rule_reads_no_run_file_no_store_and_no_known_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("--rule read a run file, a store or a known answer")

    for target in (
        "orgos.eval.runs.read_run_files",
        "orgos.eval.runs.read_versions",
        "orgos.eval.runs.read_plan",
        "orgos.eval.preflight.record_load_id",
        "orgos.eval.preflight.preflight",
        "orgos.eval.gold.known_answers",
        "orgos.cli.load_settings",
        "orgos.cli.read_run_files",
    ):
        monkeypatch.setattr(target, refuse)
    monkeypatch.chdir(REPO)
    with pytest.raises(SystemExit) as exit_info:
        app(args=["eval", "report", "--rule"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out.splitlines()
    assert SPEC_PER_TYPE in out
    assert sum(1 for line in out if line.startswith("  m ")) == 20
