"""Guard: `ci-status`, the one required CI check, rolls up every other ci.yml job.

A job missing from its `needs` or from its result table never blocks a merge,
and a skippable gate reads as green, since GitHub counts a skipped required
check as passing.
"""

from __future__ import annotations

import re
from typing import Any

from tests.framework_conformance.venv_job_coverage import ci_jobs

GATE_JOB = "ci-status"
# The `if` of jobs that run on ready pull requests and never in the merge queue.
PR_ONLY = (
    "github.event_name == 'pull_request' && github.event.pull_request.draft == false"
)
# One `"<job>:${{ needs.<job>.result }}:<allowed|results>"` entry of the gate's loop.
RESULT_ENTRY = re.compile(
    r'"([a-z][a-z0-9-]*):\$\{\{ needs\.\1\.result \}\}:([a-z|]+)"'
)


def allowed_results() -> dict[str, set[str]]:
    """The results the gate accepts per job, read from its verification loop."""
    script = "\n".join(step.get("run", "") for step in ci_jobs()[GATE_JOB]["steps"])
    return {
        job: set(allowed.split("|")) for job, allowed in RESULT_ENTRY.findall(script)
    }


def needed_jobs(job: dict[str, Any]) -> set[str]:
    """A job's `needs`, which YAML may give as one job id or a list."""
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def pr_only_jobs() -> set[str]:
    """Jobs gated to pull requests, plus jobs that need only such jobs."""
    jobs = ci_jobs()
    gated = {name for name, job in jobs.items() if job.get("if") == PR_ONLY}
    return gated | {
        name
        for name, job in jobs.items()
        if "if" not in job and needed_jobs(job) and needed_jobs(job) <= gated
    }


class TestCiStatusCoverage:
    def test_gate_needs_every_other_job(self) -> None:
        jobs = ci_jobs()
        assert set(jobs[GATE_JOB]["needs"]) == set(jobs) - {GATE_JOB}

    def test_gate_checks_the_result_of_every_job_it_needs(self) -> None:
        assert allowed_results().keys() == set(ci_jobs()[GATE_JOB]["needs"])

    def test_gate_fails_a_draft_run(self) -> None:
        # A draft run skips every job, lint included.
        assert allowed_results()["lint"] == {"success"}

    def test_gate_runs_whatever_its_needs_concluded(self) -> None:
        assert ci_jobs()[GATE_JOB]["if"] == "always()"

    def test_only_pull_request_only_jobs_may_skip(self) -> None:
        # Any other skipped job would leave a ready pull request green untested.
        may_skip = {
            job for job, allowed in allowed_results().items() if "skipped" in allowed
        }
        assert may_skip == pr_only_jobs()
