"""Weekly durations refresh uses an App token, in-job xdist, and PRs only on drift."""

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
    def test_runs_weekly_after_ci_and_on_demand(self) -> None:
        workflow = load_workflow()
        assert workflow["on"]["schedule"] == [{"cron": "17 4 * * 1"}]
        assert "workflow_dispatch" in workflow["on"]
        assert "pull_request" not in workflow["on"]

    def test_store_step_uses_in_job_xdist_on_one_runner(self) -> None:
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

    def test_pr_uses_the_app_token_and_only_commits_durations_on_diff(self) -> None:
        workflow = load_workflow()
        steps = workflow["jobs"]["refresh"]["steps"]
        token = next(step for step in steps if step.get("id") == "app-token")
        assert token["with"]["client-id"] == "${{ secrets.APP_CLIENT_ID }}"
        assert token["with"]["private-key"] == "${{ secrets.APP_PRIVATE_KEY }}"
        pr = next(
            step
            for step in steps
            if step.get("name") == "Open a durations PR when the file drifted"
        )
        script = pr["run"]
        assert 'git add -- "$DURATIONS_PATH"' in script
        assert "git diff --cached --quiet" in script
        assert "gh pr create" in script
        assert "chore/pytest-durations" in workflow["env"]["DURATIONS_BRANCH"]
        assert workflow["env"]["DURATIONS_PATH"] == DURATIONS_PATH_NAME
        assert pr["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
