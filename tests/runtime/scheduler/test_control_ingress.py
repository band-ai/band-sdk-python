"""Agent decline routing and refusal recovery through a running consumer."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.conftest import Waiting
from tests.runtime.scheduler.kit import (
    IngressAdapter,
    LateDecliningAdapter,
    declining_room,
)


class TestControlIngress:
    async def test_declined_control_falls_back_once_and_completes(self) -> None:
        adapter = IngressAdapter()
        async with declining_room(adapter, started=True, batching=False) as (peer, _, _):
            original = await adapter.fallbacks.get()
            assert original.id == "expired"
            assert peer.events_posted == 1
            assert not peer.writes_of(DeliveryStatus.PROCESSED)
            adapter.release_fallback.set()
            await Waiting.until(
                lambda: peer.status_of(original.id) is DeliveryStatus.PROCESSED
            )
            assert adapter.ordinary == [original.id]
            assert adapter.fallbacks.empty()
            assert peer.events_posted == 1

    @pytest.mark.parametrize(
        "recognized", [False, True], ids=["ordinary-first", "control-first"]
    )
    async def test_refused_original_keeps_its_only_attempt_for_resync(
        self, recognized: bool
    ) -> None:
        adapter = IngressAdapter(recognized=recognized, refuse=True)
        async with declining_room(adapter, started=True, batching=False) as (peer, ctx, _):
            original = await adapter.fallbacks.get()
            await ctx.on_event(peer.post(adapter.probe_id))
            adapter.release_fallback.set()
            await adapter.probe_entered.wait()
            assert adapter.ordinary == [original.id]
            assert peer.events_posted == 1
            assert peer.status_of(original.id) is DeliveryStatus.PROCESSING
            assert not peer.writes_of(DeliveryStatus.PROCESSED, original.id)

            adapter.refuse = False
            await ctx.request_resync()
            adapter.release_probe.set()
            await Waiting.until(
                lambda: peer.status_of(original.id) is DeliveryStatus.PROCESSED
            )
            recovered = await adapter.fallbacks.get()
            assert recovered == original
            assert adapter.ordinary == [original.id, original.id]
            assert len(peer.writes_of(DeliveryStatus.PROCESSED, original.id)) == 1
            assert peer.events_posted == 2

    async def test_late_decline_after_stop_does_not_suppress_reoffering(
        self,
    ) -> None:
        adapter = LateDecliningAdapter()
        async with declining_room(adapter, started=True, batching=False) as (peer, ctx, _):
            try:
                await adapter.entered.wait()
                ctx.stop_room()
                await adapter.cancelled.wait()
                await ctx.resume_room()
            finally:
                adapter.release.set()
            await Waiting.until(lambda: len(adapter.offers) == 2)
            await Waiting.until(
                lambda: peer.status_of("expired") is DeliveryStatus.PROCESSED
            )
            assert adapter.offers == ["expired", "expired"]
            assert not adapter.ordinary
