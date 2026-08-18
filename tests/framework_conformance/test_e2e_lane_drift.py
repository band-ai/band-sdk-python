"""Guard against e2e-workflow lane drift.

CI lanes are derived from the adapter registry (``ci_lanes``), but the workflow's
backend-setup steps are gated by ``matrix.lane == '<id>'`` literals, and its
``workflow_dispatch`` ``lane`` dropdown is a hand-maintained option list. If either
drifts from the registry — a gate naming a removed lane never runs its step, a
dropdown missing a lane can't dispatch-select it — the drift is otherwise invisible.
These run in the normal unit suite (``tests/e2e/`` is excluded there), so the drift
fails loudly on every PR, not only on a manual workflow dispatch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.e2e.baseline.toolkit.ci_lanes import (
    E2E_WORKFLOW,
    assert_workflow_lane_gates_known,
    assert_workflow_lane_options_match_registry,
    ci_lanes,
    workflow_lane_gate_ids,
)


_RELEASE_GATE_WORKFLOW = E2E_WORKFLOW.parent / "release-gate.yml"


def _jobs(workflow_path: Path) -> dict[str, Any]:
    return yaml.safe_load(workflow_path.read_text(encoding="utf-8"))["jobs"]


def _workflow_jobs() -> dict[str, Any]:
    return _jobs(E2E_WORKFLOW)


def test_workflow_lane_gates_reference_only_known_lanes() -> None:
    """Every ``matrix.lane`` gate in e2e.yml names a lane the registry emits."""
    assert_workflow_lane_gates_known()


def test_workflow_lane_extraction_is_not_vacuous() -> None:
    """The gate extraction actually matches something — otherwise the guard above
    would pass vacuously. The consolidated backend setup is gated on ``backends``."""
    gates = workflow_lane_gate_ids()
    assert gates, "no matrix.lane gates found in e2e.yml — the regex likely drifted"
    assert "backends" in gates
    assert gates <= {str(cl.id) for cl in ci_lanes()}


def test_workflow_lane_options_match_registry() -> None:
    """The dispatch ``lane`` dropdown lists exactly the registry lanes plus ``all``."""
    assert_workflow_lane_options_match_registry()


def _step_index(job: dict[str, Any], name: str) -> int:
    """The position of ``name`` in ``job``'s steps.

    Reads names with ``.get`` — ``name:`` is optional in a step, so indexing would
    raise ``KeyError`` from an unrelated unnamed step instead of this test's own
    assertion about the step it actually cares about.
    """
    for i, step in enumerate(job["steps"]):
        if step.get("name") == name:
            return i
    raise AssertionError(f"no step named {name!r} in the job")


def test_baseline_certification_checks_out_before_running_repo_scripts() -> None:
    """The independent status job must have the repository scripts it invokes."""
    job = _workflow_jobs()["mark-baseline"]

    assert job["permissions"]["contents"] == "read"
    assert _step_index(job, "Checkout code") < _step_index(
        job, "Mark the commit baseline-green or baseline-red"
    )


def test_scoped_manual_runs_report_without_certifying_the_baseline() -> None:
    """A partial run has a requester-only report path, separate from release state."""
    job = _workflow_jobs()["report-scoped-run"]

    assert "workflow_dispatch" in job["if"]
    assert "github.triggering_actor" in job["env"]["RECIPIENTS"]
    assert "does not affect the release gate" in job["env"]["SCOPE_NOTICE"]
    assert job["permissions"]["contents"] == "read"


# The jobs that report a run's outcome to the outside world: a commit status, and a
# comment on a tracking issue. Their `if:` conditions decide *whether the baseline
# gets certified at all*, so the two guards below pin the parts that fail silently.
_REPORTING_JOBS = ("mark-baseline", "report-scoped-run")


@pytest.mark.parametrize("job_name", _REPORTING_JOBS)
def test_reporting_jobs_do_not_certify_a_cancelled_run(job_name: str) -> None:
    """A cancelled run is not evidence, so it must not post a status or comment.

    Under ``always()`` a nightly whose legs were cancelled mid-flight (the
    per-(lane,OS) concurrency group, or a human cancelling) reports the baseline as
    *red* and reopens the tracking issue with nothing actually broken. Declining to
    report instead leaves the commit with no status at all, so
    ``check-release-baseline.sh``'s backward scan skips it and gates on whichever
    earlier commit was actually tested (bounded by ``MAX_BASELINE_AGE_DAYS``) —
    not a ``pending`` block, a fall-back to the last real evidence. A
    ``timeout-minutes`` leg kill does not cancel the run, so it still reddens.
    """
    condition = _workflow_jobs()[job_name]["if"]

    assert "!cancelled()" in condition
    assert "always()" not in condition


def test_release_gate_checks_out_before_running_repo_scripts() -> None:
    """The release gate invokes a repository script, so it must check the repo out.

    Same failure mode the mark-baseline guard above covers: without a checkout the
    step dies on a missing file, which on a *required* check blocks every PR in the
    repository rather than just misreporting one run.
    """
    job = _jobs(_RELEASE_GATE_WORKFLOW)["release-baseline-gate"]
    steps = job["steps"]

    checkout = _step_index(job, "Checkout code")
    invokes_script = next(
        i for i, step in enumerate(steps) if ".github/scripts/" in step.get("run", "")
    )

    assert checkout < invokes_script


def test_mark_baseline_only_certifies_the_default_branch() -> None:
    """A full-matrix ``workflow_dispatch`` from a feature branch must not certify it.

    ``schedule`` always fires on the default branch, but a user can dispatch from any
    branch in the Actions UI — without this check a feature-branch run would post
    ``baseline-green`` on its own ``github.sha``, which (this repo allows merge and
    rebase merges) can later land in main's history verbatim and vouch for main.
    """
    condition = _workflow_jobs()["mark-baseline"]["if"]

    assert (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
        in condition
    )


def test_e2e_concurrency_group_is_scoped_by_trigger_kind() -> None:
    """A scoped manual dispatch must never cancel — and falsely redden — a nightly
    leg for the same lane+OS (or vice versa); each trigger kind only supersedes its
    own prior run.
    """
    group = _workflow_jobs()["e2e"]["concurrency"]["group"]

    assert "github.event_name" in group
    assert "matrix.lane" in group
    assert "matrix.os" in group


@pytest.mark.parametrize("job_name", _REPORTING_JOBS)
def test_reporting_jobs_normalize_the_dispatch_inputs(job_name: str) -> None:
    """Both jobs must default a missing dispatch input to ``all``, like every other
    consumer (the ``lanes``/``e2e`` jobs read ``inputs.lane || 'all'``).

    Compared bare, an input that resolved empty reads as "scoped" — so a full-matrix
    run would silently never certify the commit even though the matrix did fan out.
    """
    condition = _workflow_jobs()[job_name]["if"]

    for dimension in ("lane", "os"):
        assert f"github.event.inputs.{dimension} || 'all'" in condition
