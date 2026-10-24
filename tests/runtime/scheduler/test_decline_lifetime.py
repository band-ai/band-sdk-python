"""A late control result cannot suppress handling in a new pass or revision."""

from __future__ import annotations

import asyncio

from tests.runtime.scheduler.kit import WaitingAdapter, declining_room

LATE_CONTROL = "late"


class TestDeclineLifetime:
    async def test_a_late_false_does_not_decline_a_new_revision(self) -> None:
        adapter = WaitingAdapter()
        async with declining_room(adapter) as (peer, ctx, callback):
            event = peer.post(LATE_CONTROL)
            with ctx.scheduler.discovery():
                late = asyncio.create_task(callback(ctx, event))
                await adapter.entered.wait()
                ctx.interrupt()
                adapter.release.set()
                assert await late is False
                assert await callback(ctx, event) is False
            assert peer.events_posted == 2

    async def test_a_late_false_does_not_decline_a_new_pass(self) -> None:
        adapter = WaitingAdapter()
        async with declining_room(adapter) as (peer, ctx, callback):
            event = peer.post(LATE_CONTROL)
            with ctx.scheduler.discovery():
                late = asyncio.create_task(callback(ctx, event))
                await adapter.entered.wait()
            with ctx.scheduler.discovery():
                adapter.release.set()
                assert await late is False
                assert await callback(ctx, event) is False
            assert peer.events_posted == 2
