"""Guard against e2e-workflow lane drift.

CI lanes are derived from the adapter registry (``ci_lanes``), but the workflow's
backend-setup steps are gated by ``matrix.lane == '<id>'`` literals. If a lane is
renamed/removed in the registry but a gate still names the old id, that gate is
never true and its step silently never runs (e.g. the codex-acp E2E would vanish).
These run in the normal unit suite (``tests/e2e/`` is excluded there), so the drift
fails loudly on every PR, not only on a manual workflow dispatch.
"""

from __future__ import annotations

from tests.e2e.baseline.toolkit.adapters import (
    assert_workflow_lane_gates_known,
    ci_lanes,
    workflow_lane_gate_ids,
)


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
