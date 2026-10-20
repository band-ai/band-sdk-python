"""Guard: `ci-status`, the one required CI check, rolls up every other ci.yml job.

A job missing from its `needs` never blocks a merge, and a skippable gate reads
as green, since GitHub counts a skipped required check as passing.
"""

from __future__ import annotations

from typing import Any

import yaml

from tests.framework_conformance.venv_job_coverage import CI_WORKFLOW

GATE_JOB = "ci-status"


def ci_jobs() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


class TestCiStatusCoverage:
    def test_gate_needs_every_other_job(self) -> None:
        jobs = ci_jobs()
        assert set(jobs[GATE_JOB]["needs"]) == set(jobs) - {GATE_JOB}

    def test_gate_runs_whatever_its_needs_concluded(self) -> None:
        assert ci_jobs()[GATE_JOB]["if"] == "always()"
