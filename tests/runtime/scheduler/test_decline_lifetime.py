"""A late control result cannot suppress handling in a new pass or revision."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

import pytest

from tests.runtime.scheduler.kit import WaitingAdapter, declining_room


class TestDeclineLifetime:
    @pytest.mark.parametrize(
        "replace_pass", [False, True], ids=["new-revision", "new-pass"]
    )
    async def test_a_late_false_does_not_decline_the_current_control(
        self, replace_pass: bool
    ) -> None:
        adapter = WaitingAdapter()
        async with declining_room(adapter) as (peer, ctx, callback):
            event = peer.post("late")
            with ExitStack() as passes:
                passes.enter_context(ctx.scheduler.discovery())
                late = asyncio.create_task(callback(ctx, event))
                await adapter.entered.wait()
                if replace_pass:
                    passes.close()
                    passes.enter_context(ctx.scheduler.discovery())
                else:
                    ctx.interrupt()
                adapter.release.set()
                assert await late is False
                assert await callback(ctx, event) is False
            assert peer.events_posted == 2
