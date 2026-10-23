"""Weekly durations refresh uses an App token, serial pytest, and PRs only on drift."""

from __future__ import annotations

from typing import Any

import yaml

from tests.paths import REPO_ROOT

WORKFLOW_PATH = REPO_ROOT / ".github/workflows/pytest-durations.yml"


def load_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class TestPytestDurationsWorkflow:
    def test_runs_weekly_after_ci_and_on_demand(self) -> None:
        workflow = load_workflow()
        assert workflow["on"]["schedule"] == [{"cron": "17 4 * * 1"}]
        assert "workflow_dispatch" in workflow["on"]
        assert "pull_request" not in workflow["on"]

    def test_store_step_is_serial_and_writes_the_committed_durations_file(
        self,
    ) -> None:
        store = next(
            step
            for step in load_workflow()["jobs"]["refresh"]["steps"]
            if step.get("name") == "Store unit-test durations"
        )
        script = store["run"]
        assert "pytest -n0" in script
        assert "--store-durations" in script
        assert "--clean-durations" in script
        assert '--durations-path "$DURATIONS_PATH"' in script
        assert "--ignore=tests/e2e" in script
        assert "--ignore=tests/integration" in script
        assert "-n auto" not in script

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
        assert pr["env"]["GH_TOKEN"] == "${{ steps.app-token.outputs.token }}"
