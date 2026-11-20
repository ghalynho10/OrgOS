"""The publish workflow: check first, then only the page and its stylesheet (spec 0016 AC-24).

Reads `.github/workflows/pages.yml` as data; runs nothing and calls no network.
"""

from typing import Any

import yaml

from tests.site_pages import REPO

WORKFLOW = REPO / ".github" / "workflows" / "pages.yml"


def _workflow() -> Any:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(workflow: Any) -> Any:
    # YAML 1.1 reads a bare `on` key as the boolean True.
    return workflow.get("on", workflow.get(True))


def _step_index(steps: list[Any], predicate: Any) -> int:
    found = [index for index, step in enumerate(steps) if predicate(step)]
    assert len(found) == 1, found
    return found[0]


def _runs(step: Any, command: str) -> bool:
    return command in str(step.get("run", ""))


def _uses(step: Any, action: str) -> bool:
    return str(step.get("uses", "")).startswith(action + "@")


# covers: AC-24
def test_check_runs_before_anything_is_staged_or_uploaded() -> None:
    steps = _workflow()["jobs"]["build"]["steps"]
    check = _step_index(steps, lambda s: _runs(s, "uv run orgos site check"))
    stage = _step_index(steps, lambda s: _runs(s, "_site"))
    upload = _step_index(steps, lambda s: _uses(s, "actions/upload-pages-artifact"))
    assert check < stage < upload
    assert steps[upload]["with"]["path"] == "_site"


# covers: AC-24
def test_only_index_and_style_are_staged() -> None:
    steps = _workflow()["jobs"]["build"]["steps"]
    stage = steps[_step_index(steps, lambda s: _runs(s, "_site"))]["run"]
    copies = [line.split() for line in stage.splitlines() if line.strip().startswith("cp ")]
    assert copies == [["cp", "site/index.html", "site/style.css", "_site/"]]
    assert "conclusion.md" not in stage


# covers: AC-24
def test_deploys_through_github_actions_after_the_check_job() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert [_uses(step, "actions/deploy-pages") for step in deploy["steps"]] == [True]


# covers: AC-24
def test_triggers_are_a_push_to_main_on_the_page_or_report_and_a_manual_run() -> None:
    triggers = _triggers(_workflow())
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    assert sorted(triggers["push"]["paths"]) == sorted(["site/**", "results/findings/report.json"])
