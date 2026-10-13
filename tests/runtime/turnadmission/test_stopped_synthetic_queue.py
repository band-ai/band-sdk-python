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
        peer: LifecyclePlatform,
        adapter: TurnRecorder,
        setup_gate: ResponseGate,
    ) -> None:
        await ctx.start()
        await queue_while_stopped(ctx, peer, "local-A", "local-B")
        setup_gate.release.clear()
        await play(ctx, peer)
        await setup_gate.entered.wait()
        peer.stopped = True
        ctx.stop_room()
        setup_gate.release.set()

        await wait_for_condition(lambda: turn_settled(ctx))
        assert adapter.completed == []
        assert queued_synthetic(ctx) == ["local-A", "local-B"]
        assert peer.posts == []

        await play(ctx, peer)
        await wait_for_condition(lambda: adapter.completed == ["local-A", "local-B"])

    async def test_stop_during_queued_synthetic_handler_ends_pass_and_replays_in_order(
        self,
        ctx: ExecutionContext,
        peer: LifecyclePlatform,
        adapter: TurnRecorder,
    ) -> None:
        await ctx.start()
        await queue_while_stopped(ctx, peer, "local-A", "local-B")
        adapter.release.clear()
        await play(ctx, peer)
        await adapter.entered.wait()
        peer.stopped = True
        ctx.stop_room()

        await wait_for_condition(lambda: turn_settled(ctx))
        assert adapter.completed == []
        assert queued_synthetic(ctx) == ["local-A", "local-B"]

        adapter.release.set()
        await play(ctx, peer)
        await wait_for_condition(lambda: adapter.completed == ["local-A", "local-B"])
