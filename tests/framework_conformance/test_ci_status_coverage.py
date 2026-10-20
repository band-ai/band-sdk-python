"""Guard: `ci-status`, the one required CI check, rolls up every other ci.yml job.

A job missing from its `needs` never blocks a merge, and a skippable gate reads
as green, since GitHub counts a skipped required check as passing.
"""

from __future__ import annotations

from tests.framework_conformance.venv_job_coverage import ci_jobs

GATE_JOB = "ci-status"


class TestCiStatusCoverage:
    def test_gate_needs_every_other_job(self) -> None:
        jobs = ci_jobs()
        assert set(jobs[GATE_JOB]["needs"]) == set(jobs) - {GATE_JOB}

    def test_gate_runs_whatever_its_needs_concluded(self) -> None:
        assert ci_jobs()[GATE_JOB]["if"] == "always()"

    def test_gate_fails_on_a_failed_job_or_a_draft_run(self) -> None:
        failing_conditions = [
            step["if"]
            for step in ci_jobs()[GATE_JOB]["steps"]
            if step["run"].rstrip().endswith("exit 1")
        ]
        assert failing_conditions == [
            "contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')",
            "needs.lint.result != 'success'",
        ]
