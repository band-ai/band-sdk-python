"""Paired budgets for baseline tests that await slow live turns.

``tests.toolkit.timeouts`` owns the generic rule: the hard pytest-timeout backstop
sits at ``E2E_TIMEOUT`` plus ``max(extra, TIMEOUT_BACKSTOP_MARGIN_S)``, so the soft
barrier raises its diagnostic ``TimeoutError`` first and the backstop only ever
catches a genuine hang. That rule holds only if a test's ``extra=`` accounts for
*every* soft barrier it awaits: a test with two 240s barriers under a 240s backstop
reports a phase-less ``Timeout >240.0s`` instead of naming the stalled turn.

So a test that awaits several slow-backend turns must size its barrier deadline and
its ``extra=`` from one fact. :func:`slow_turn_budget` is that fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.toolkit.timeouts import TIMEOUT_BACKSTOP_MARGIN_S

# The longest a Band adapter lets a single live turn run before aborting it itself:
# the largest ``turn_timeout_s`` among the matrix adapters (opencode and letta both
# use 300s; codex 180s, copilot_sdk 120s). Below it a barrier declares a turn that
# is still healthy "stuck" -- the loaded backend lanes funnel concurrent turns
# through one shared serve against a throttled free model, and those turns are slow,
# not dead. At it the adapter has already given up, so no reply can still be coming.
SLOW_TURN_DEADLINE_S = 300.0


@dataclass(frozen=True)
class SlowTurnBudget:
    """A slow-backend test's two deadlines, derived together so they can't drift.

    ``deadline_s`` goes to each barrier's ``deadline_s=``; ``extra_s`` goes to
    ``@pytest.mark.timeout(extra=...)``.
    """

    deadline_s: float
    extra_s: int


def slow_turn_budget(turn_budget_s: int, *, barriers: int) -> SlowTurnBudget:
    """Budget a test that awaits ``barriers`` sequential slow-live-turn barriers.

    Every barrier gets :data:`SLOW_TURN_DEADLINE_S`, and ``extra_s`` lifts the hard
    backstop above all of them plus the usual margin for provisioning and teardown —
    so a stall surfaces as the barrier's ``TimeoutError`` naming the phase, never as
    a bare pytest-timeout kill.
    """
    total = SLOW_TURN_DEADLINE_S * barriers + TIMEOUT_BACKSTOP_MARGIN_S
    return SlowTurnBudget(
        deadline_s=SLOW_TURN_DEADLINE_S,
        extra_s=max(int(total) - turn_budget_s, TIMEOUT_BACKSTOP_MARGIN_S),
    )
