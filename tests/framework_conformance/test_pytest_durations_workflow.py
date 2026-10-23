"""Durations rebalance is a weekly bot PR, not extra pull-request CI jobs."""

from __future__ import annotations

from typing import Any

import yaml

from tests.paths import REPO_ROOT
from tests.testdurations import CLEAN_FLAG, DURATIONS_PATH_NAME, PATH_FLAG, STORE_FLAG
from tests.xdistworker import CI_XDIST_FLAGS

WORKFLOW_PATH = REPO_ROOT / ".github/workflows/pytest-durations.yml"


def load_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class TestPytestDurationsWorkflow:
    def test_runs_weekly_after_ci_and_on_demand_not_on_pull_requests(self) -> None:
        workflow = load_workflow()
        # Weekly CI is 03:17 Mondays; this job follows at 04:17 so it times a
        # quiet tree rather than racing the suite it is measuring.
        assert workflow["on"]["schedule"] == [{"cron": "17 4 * * 1"}]
        assert "workflow_dispatch" in workflow["on"]
        assert "pull_request" not in workflow["on"]

    def test_store_uses_in_job_xdist_on_the_unit_suite(self) -> None:
        store = next(
            step
            for step in load_workflow()["jobs"]["refresh"]["steps"]
            if step.get("name") == "Store unit-test durations"
        )
        script = store["run"]
        for flag in CI_XDIST_FLAGS:
            assert flag in script
        assert STORE_FLAG in script
        assert CLEAN_FLAG in script
        assert f'{PATH_FLAG} "$DURATIONS_PATH"' in script
        assert "--ignore=tests/e2e" in script
        assert "--ignore=tests/integration" in script
        assert "-n0" not in script
        assert "--splits" not in script

    def test_bot_opens_a_main_pr_only_when_durations_drift(self) -> None:
        workflow = load_workflow()
        steps = workflow["jobs"]["refresh"]["steps"]
        token = next(step for step in steps if step.get("id") == "app-token")
        assert token["with"]["client-id"] == "${{ secrets.APP_CLIENT_ID }}"
        assert token["with"]["private-key"] == "${{ secrets.APP_PRIVATE_KEY }}"
        checkout = next(step for step in steps if step.get("name") == "Checkout main")
        assert checkout["with"]["ref"] == "main"
        pr = next(
            step
            for step in steps
            if step.get("name") == "Open a durations PR when the file drifted"
        )
        script = pr["run"]
        assert 'git add -- "$DURATIONS_PATH"' in script
        assert "git diff --cached --quiet" in script
        assert "gh pr create" in script
        assert "--base main" in script
        assert workflow["env"]["DURATIONS_PATH"] == DURATIONS_PATH_NAME
        assert pr["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
