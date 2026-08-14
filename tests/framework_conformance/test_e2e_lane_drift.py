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

from typing import Any

import yaml

from tests.e2e.baseline.toolkit.ci_lanes import (
    E2E_WORKFLOW,
    assert_workflow_lane_gates_known,
    assert_workflow_lane_options_match_registry,
    ci_lanes,
    workflow_lane_gate_ids,
)


def _workflow_jobs() -> dict[str, Any]:
    return yaml.safe_load(E2E_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


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


def test_baseline_certification_checks_out_before_running_repo_scripts() -> None:
    """The independent status job must have the repository scripts it invokes."""
    job = _workflow_jobs()["mark-baseline"]
    steps = job["steps"]
    checkout = next(
        i for i, step in enumerate(steps) if step["name"] == "Checkout code"
    )
    status = next(
        i
        for i, step in enumerate(steps)
        if step["name"] == "Mark the commit baseline-green or baseline-red"
    )

    assert job["permissions"]["contents"] == "read"
    assert checkout < status


def test_scoped_manual_runs_report_without_certifying_the_baseline() -> None:
    """A partial run has a requester-only report path, separate from release state."""
    job = _workflow_jobs()["report-scoped-run"]

    assert "workflow_dispatch" in job["if"]
    assert "github.triggering_actor" in job["env"]["RECIPIENTS"]
    assert "does not affect the release gate" in job["env"]["SCOPE_NOTICE"]
    assert job["permissions"]["contents"] == "read"
