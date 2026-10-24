"""Native control owns settlement even after an admitted callback declines."""

from __future__ import annotations

from band.client.streaming import DeliveryStatus
from band.runtime.execution import ExecutionState
from tests.runtime.conftest import Waiting
from tests.runtime.scheduler.controlintent import DecliningControlAdapter
from tests.runtime.scheduler.kit import DecliningAdapter, declining_room


class TestControlIntent:
    async def test_interrupt_after_decline_completes_original(self) -> None:
        adapter = DecliningControlAdapter()
        async with declining_room(adapter) as (peer, ctx, _):
            adapter.after_decline(ctx.interrupt)
            await ctx.scheduler.run_pass(recovery=True)

            assert adapter.results.get_nowait() is True
            assert peer.status_of("expired") is DeliveryStatus.PROCESSED
            assert len(peer.writes_of(DeliveryStatus.PROCESSED, "expired")) == 1
            assert adapter.ordinary == []

    async def test_interrupted_decline_is_not_replayed_on_recovery(self) -> None:
        adapter = DecliningControlAdapter()
        async with declining_room(adapter) as (peer, ctx, _):
            adapter.after_decline(ctx.interrupt)
            await ctx.scheduler.run_pass(recovery=True)
            assert adapter.results.get_nowait() is True

            await ctx.scheduler.run_pass(recovery=True)

            assert adapter.ordinary == []
            assert peer.events_posted == 1
            assert len(peer.writes_of(DeliveryStatus.PROCESSED, "expired")) == 1

    async def test_running_consumer_settles_interrupt_after_decline(self) -> None:
        adapter = DecliningControlAdapter()
        async with declining_room(adapter, started=True) as (peer, ctx, _):
            adapter.after_decline(ctx.interrupt)
            assert await adapter.results.get() is True
            await Waiting.until(lambda: ctx.state is ExecutionState.IDLE)

            assert peer.status_of("expired") is DeliveryStatus.PROCESSED
            assert len(peer.writes_of(DeliveryStatus.PROCESSED, "expired")) == 1
            assert adapter.ordinary == []

    async def test_normal_decline_still_falls_back_to_ordinary_delivery(self) -> None:
        adapter = DecliningAdapter()
        async with declining_room(adapter) as (peer, ctx, _):
            await ctx.scheduler.run_pass(recovery=True)

            assert peer.status_of("expired") is DeliveryStatus.PROCESSED
            assert adapter.ordinary == ["expired"]
            assert peer.events_posted == 1

    async def test_stop_after_decline_preserves_original_for_resume(self) -> None:
        adapter = DecliningControlAdapter()
        async with declining_room(adapter) as (peer, ctx, _):
            adapter.after_decline(ctx.stop_room)
            await ctx.scheduler.run_pass(recovery=True)

            assert peer.status_of("expired") is DeliveryStatus.PROCESSING
            assert peer.writes_of(DeliveryStatus.PROCESSED, "expired") == []
            assert adapter.ordinary == []

            await ctx.resume_room()
            await ctx.scheduler.run_pass(recovery=True)

            assert peer.status_of("expired") is DeliveryStatus.PROCESSED
            assert adapter.ordinary == ["expired"]
