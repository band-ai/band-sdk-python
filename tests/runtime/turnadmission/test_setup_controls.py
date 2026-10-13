"""A control during a begun turn's setup terminates the turn before its handler."""

from __future__ import annotations

import pytest

from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import (
    ROOM_ID,
    LifecyclePlatform,
    ResponseGate,
    synthetic_message,
)
from tests.runtime.turnadmission.kit import (
    TurnRecorder,
    queued_synthetic,
    reconcile_once,
    started,
    turn_settled,
)


@pytest.mark.timeout(10)
class TestSetupTimeControls:
    """STOP or INTERRUPT is never lost once a message turn has begun."""

    async def test_stop_during_websocket_setup_never_runs_real_message(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
        setup_gate: ResponseGate,
    ) -> None:
        await started(ctx)
        setup_gate.release.clear()
        platform.add_message("B")
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="B"))
        await setup_gate.entered.wait()
        # The platform still accepts the claim: it raced the STOP.
        ctx.stop_room()
        setup_gate.release.set()

        await wait_for_condition(lambda: turn_settled(ctx))
        assert adapter.completed == []
        assert platform.marked("processed") == []

        await ctx.resume_room()
        await wait_for_condition(lambda: platform.marked("processed") == ["B"])
        assert adapter.completed == ["B"]

    async def test_interrupt_during_synthetic_setup_consumes_the_turn(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
        setup_gate: ResponseGate,
    ) -> None:
        await started(ctx)
        setup_gate.release.clear()
        await ctx.on_event(synthetic_message("local-A"))
        await setup_gate.entered.wait()
        ctx.interrupt()
        setup_gate.release.set()

        await wait_for_condition(lambda: turn_settled(ctx))
        await reconcile_once(ctx, platform)
        assert adapter.completed == []
        assert queued_synthetic(ctx) == []
