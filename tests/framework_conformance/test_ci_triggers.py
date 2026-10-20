"""Guard: CI runs on pull requests and the merge queue, never on a push to `main`.

`main` only changes through the merge queue, which validates the exact commit
that lands, so CI never re-runs on a push to `main` and Core coverage stays out
of the queue. A new push to a pull request supersedes its running CI, while
every merge-queue run gets a concurrency group of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.framework_conformance.venv_job_coverage import CI_WORKFLOW
from tests.paths import REPO_ROOT

COVERAGE_WORKFLOW = REPO_ROOT / ".github/workflows/python-core-coverage.yml"


def workflow(path: Path) -> dict[str, Any]:
    # BaseLoader keeps `on` a string key instead of YAML 1.1's boolean True.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


class TestCiTriggers:
    def test_ci_runs_on_pull_requests_and_the_merge_queue_only(self) -> None:
        assert set(workflow(CI_WORKFLOW)["on"]) == {"pull_request", "merge_group"}

    def test_only_pull_request_runs_are_cancelled(self) -> None:
        # A requeue on the same base reuses the queue ref, so queue runs group by run id.
        assert workflow(CI_WORKFLOW)["concurrency"] == {
            "group": "${{ github.event_name == 'pull_request' && "
            "format('{0}-{1}', github.workflow, github.ref) || github.run_id }}",
            "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
        }

    def test_core_coverage_stays_out_of_the_merge_queue(self) -> None:
        assert "merge_group" not in workflow(COVERAGE_WORKFLOW)["on"]
