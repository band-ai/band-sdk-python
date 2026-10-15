"""Fresh context precedes admission; old context cannot override fresh discovery."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.scheduler.hydration import observe_handoff


class TestHydrationHandoff:
    @pytest.mark.parametrize("batching", [False])
    @pytest.mark.parametrize("control", [False, True], ids=["ordinary", "priority"])
    @pytest.mark.parametrize("warm", [False, True], ids=["new-context", "old-cache"])
    @pytest.mark.parametrize(
        "history",
        [DeliveryStatus.PROCESSING, DeliveryStatus.PROCESSED, DeliveryStatus.FAILED],
    )
    @pytest.mark.parametrize("stale", [DeliveryStatus.FAILED, DeliveryStatus.PROCESSED])
    async def test_context_read_order_and_admitted_membership(
        self,
        batching: bool,
        control: bool,
        warm: bool,
        history: DeliveryStatus,
        stale: DeliveryStatus,
    ) -> None:
        observed = await observe_handoff(
            batching=batching, control=control, warm=warm, history=history, stale=stale
        )
        expected = (
            ("control" if control else "ordinary",)
            if control
            or warm
            or history is DeliveryStatus.PROCESSING
            or (not batching and history is DeliveryStatus.FAILED)
            else ()
        )
        assert observed.effects == expected
        assert observed.original_preserved
        assert observed.history_reads == 1
        assert observed.target_writes == (
            ("processing target", "processed target") if expected else ()
        )
        if control:
            assert observed.blocked_attempts >= 1
            assert observed.blocked_settlements == 0

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
        assert observed.history_reads == int(warm or not batching)
