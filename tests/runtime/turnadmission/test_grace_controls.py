"""A control while a turn waits to begin keeps that turn from starting."""

from __future__ import annotations

import asyncio

import pytest

from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import ROOM_ID, LifecyclePlatform, synthetic_message
from tests.runtime.scheduler.driver import attempt_delivery
from tests.runtime.turnadmission.kit import (
    APPROVAL,
    TurnRecorder,
    play,
    queued_synthetic,
    started,
    stop_room,
)

# Inside the begin grace, so the observer finishing still lets the turn begin.
WITHIN_GRACE = CYCLE_CANCEL_GRACE_SECONDS / 2


# Real time avoids looptime rounding randomized reconciliation deadlines down.
@pytest.mark.timeout(10)
class TestGraceControls:
    """STOP during the platform-stop observer's grace leaves the next turn unstarted."""

    async def test_stop_during_turn_grace_leaves_real_message_unclaimed(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
        stop_observer: asyncio.Event,
    ) -> None:
        platform.add_message("B")
        turn = asyncio.create_task(
            attempt_delivery(ctx, make_message_event(room_id=ROOM_ID, msg_id="B"))
        )
        await asyncio.sleep(WITHIN_GRACE)
        ctx.stop_room()
        stop_observer.set()
        await turn

        assert adapter.completed == []
        assert platform.requested("processing") == []

        await ctx.resume_room()
        await ctx.scheduler.run_pass(recovery=True)
        assert adapter.completed == ["B"]

    async def test_stop_during_turn_grace_keeps_synthetic_message_queued(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
        stop_observer: asyncio.Event,
    ) -> None:
        await started(ctx)
        await ctx.on_event(synthetic_message("local-A"))
        await asyncio.sleep(WITHIN_GRACE)
        stop_room(ctx, platform)
        stop_observer.set()
        await asyncio.sleep(CYCLE_CANCEL_GRACE_SECONDS)

        assert adapter.completed == []
        assert queued_synthetic(ctx) == ["local-A"]

        await play(ctx, platform)
        assert await adapter.next_completion() == "local-A"
        assert adapter.completed == ["local-A"]

    async def test_stop_during_turn_grace_skips_control_probe(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
        stop_observer: asyncio.Event,
    ) -> None:
        platform.add_message("B")
        platform.add_message("C", content=APPROVAL)
        ctx.scheduler.defer("B")
        scan = asyncio.create_task(ctx.scheduler.run_pass())
        # A STOP before the page returns ends the scan on its revision check.
        await wait_for_condition(lambda: platform.actionable_cursors != [])
        await asyncio.sleep(WITHIN_GRACE)
        ctx.stop_room()
        stop_observer.set()
        await scan

        assert adapter.controls == []
        assert platform.requested("processing") == []
