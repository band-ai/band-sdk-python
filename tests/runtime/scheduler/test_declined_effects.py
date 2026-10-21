"""Control and ordinary routing share one disposable decline decision."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.scheduler.kit import DecliningAdapter, declining_room


class TestDeclinedEffects:
    async def test_decline_posts_one_error_before_ordinary_fallback(self) -> None:
        adapter = DecliningAdapter()
        async with declining_room(adapter) as (peer, ctx, _):
            await ctx.scheduler.run_pass(recovery=True)
            assert peer.status_of("expired") is DeliveryStatus.PROCESSED
        assert adapter.ordinary == ["expired"]
        assert peer.events_posted == 1

    @pytest.mark.parametrize(
        "recognized", [False, True], ids=["ordinary-first", "control-first"]
    )
    async def test_refusal_and_priority_scan_reuse_the_decline_until_a_new_wake(
        self, recognized: bool
    ) -> None:
        adapter = DecliningAdapter(recognized=recognized, refuse=True)
        async with declining_room(adapter) as (peer, ctx, _):
            await ctx.scheduler.run_pass(recovery=True)
            assert (adapter.ordinary, peer.events_posted) == (["expired"], 1)
            assert peer.status_of("expired") is DeliveryStatus.PROCESSING
            await ctx.scheduler.run_pass(recovery=True)
            assert (adapter.ordinary, peer.events_posted) == (["expired", "expired"], 2)
            assert not peer.writes_of(DeliveryStatus.PROCESSED)
