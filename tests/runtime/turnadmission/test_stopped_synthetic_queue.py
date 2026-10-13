"""A STOP while queued synthetic work drains ends the pass and keeps the queue."""

from __future__ import annotations

import pytest

from band.runtime.execution import ExecutionContext
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import LifecyclePlatform, ResponseGate
from tests.runtime.turnadmission.kit import (
    TurnRecorder,
    play,
    queue_while_stopped,
    queued_synthetic,
    turn_settled,
)


@pytest.mark.timeout(10)
class TestStoppedSyntheticQueue:
    """A stopped room keeps its synthetic queue in order and stays responsive."""

    async def test_stop_during_synthetic_setup_keeps_loop_responsive_and_replays_after_play(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
        setup_gate: ResponseGate,
    ) -> None:
        await ctx.start()
        await queue_while_stopped(ctx, platform, "local-A", "local-B")
        setup_gate.release.clear()
        await play(ctx, platform)
        await setup_gate.entered.wait()
        platform.stopped = True
        ctx.stop_room()
        setup_gate.release.set()

        await wait_for_condition(lambda: turn_settled(ctx))
        assert adapter.completed == []
        assert queued_synthetic(ctx) == ["local-A", "local-B"]
        assert platform.posts == []

        await play(ctx, platform)
        await wait_for_condition(lambda: adapter.completed == ["local-A", "local-B"])

    async def test_stop_during_queued_synthetic_handler_ends_pass_and_replays_in_order(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
    ) -> None:
        await ctx.start()
        await queue_while_stopped(ctx, platform, "local-A", "local-B")
        adapter.release.clear()
        await play(ctx, platform)
        await adapter.entered.wait()
        platform.stopped = True
        ctx.stop_room()

        await wait_for_condition(lambda: turn_settled(ctx))
        assert adapter.completed == []
        assert queued_synthetic(ctx) == ["local-A", "local-B"]

        adapter.release.set()
        await play(ctx, platform)
        await wait_for_condition(lambda: adapter.completed == ["local-A", "local-B"])
