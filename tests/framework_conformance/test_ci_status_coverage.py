"""Guard: `ci-status`, the one required CI check, rolls up every other ci.yml job.

A job missing from its `needs` or from its result table never blocks a merge,
and a skippable gate reads as green, since GitHub counts a skipped required
check as passing.
"""

from __future__ import annotations

import re

from tests.framework_conformance.venv_job_coverage import ci_jobs

GATE_JOB = "ci-status"
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
