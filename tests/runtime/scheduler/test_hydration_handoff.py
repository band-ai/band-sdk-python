"""Fresh context precedes admission; old context cannot override fresh discovery."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.scheduler.hydration import observe_handoff

PROCESSING, PROCESSED, FAILED = (
    DeliveryStatus.PROCESSING,
    DeliveryStatus.PROCESSED,
    DeliveryStatus.FAILED,
)

# (control, warm cache, status in the fetched history) -> does the original run?
# Only a cold, ordinary original whose history shows it processed is dropped.
RUNS = [
    pytest.param(False, False, PROCESSING, True, id="ordinary-new-processing"),
    pytest.param(False, False, PROCESSED, False, id="ordinary-new-processed"),
    pytest.param(False, False, FAILED, True, id="ordinary-new-failed"),
    pytest.param(False, True, PROCESSING, True, id="ordinary-old-processing"),
    pytest.param(False, True, PROCESSED, True, id="ordinary-old-processed"),
    pytest.param(False, True, FAILED, True, id="ordinary-old-failed"),
    pytest.param(True, False, PROCESSING, True, id="priority-new-processing"),
    pytest.param(True, False, PROCESSED, True, id="priority-new-processed"),
    pytest.param(True, False, FAILED, True, id="priority-new-failed"),
    pytest.param(True, True, PROCESSING, True, id="priority-old-processing"),
    pytest.param(True, True, PROCESSED, True, id="priority-old-processed"),
    pytest.param(True, True, FAILED, True, id="priority-old-failed"),
]


class TestHydrationHandoff:
    @pytest.mark.parametrize(("control", "warm", "history", "runs"), RUNS)
    @pytest.mark.parametrize("stale", [FAILED, PROCESSED])
    async def test_context_read_order_and_admitted_membership(
        self,
        control: bool,
        warm: bool,
        history: DeliveryStatus,
        stale: DeliveryStatus,
        runs: bool,
    ) -> None:
        observed = await observe_handoff(
            batching=False, control=control, warm=warm, history=history, stale=stale
        )
        assert observed.effects == (
            ("control" if control else "ordinary",) if runs else ()
        )
        assert observed.original_preserved
        assert observed.target_writes == (
            ("processing target", "processed target") if runs else ()
        )
        if control:
            # The refused head was reached, and its refusal never completes it.
            assert observed.blocked_claimed
            assert observed.blocked_status is not PROCESSED

    @pytest.mark.parametrize("batching", [False])
    @pytest.mark.parametrize("control", [False, True], ids=["ordinary", "priority"])
    @pytest.mark.parametrize("warm", [False, True], ids=["new-context", "old-cache"])
    async def test_fresh_failed_discovery_obeys_the_configured_replay_policy(
        self, batching: bool, control: bool, warm: bool
    ) -> None:
        observed = await observe_handoff(
            batching=batching,
            control=control,
            warm=warm,
            history=DeliveryStatus.PROCESSING,
            stale=DeliveryStatus.PROCESSING,
            discovery=DeliveryStatus.FAILED,
        )
        expected = () if batching else ("control" if control else "ordinary",)
        assert observed.effects == expected
        assert observed.original_preserved
        assert observed.target_writes == (
            () if batching else ("processing target", "processed target")
        )
