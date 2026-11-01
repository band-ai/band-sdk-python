"""Room control remains independent of scoped activity-response STOP."""

from __future__ import annotations

from band.client.streaming import ControlMode
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from tests.runtime.runtimecontrol.kit import StoppingAdapter, control_payload


class TestStopNotification:
    async def test_explicit_stop_reaches_adapter_after_activity_stop(
        self,
        stopping_room: tuple[AgentRuntime, ExecutionContext],
        stopping_adapter: StoppingAdapter,
    ) -> None:
        runtime, ctx = stopping_room
        scope = await ctx.begin_delivery()
        assert scope is not None
        ctx.observe_platform_stop(scope)
        assert scope.observer_task is not None
        await scope.observer_task
        stopping_adapter.release.set()

        await runtime.handle_control(control_payload(ControlMode.STOP))

        assert stopping_adapter.heard == [("room-1", ControlMode.STOP)]
        assert ctx.is_stopped

    async def test_native_stop_reaches_the_adapter_for_a_newer_turn(
        self,
        stopping_room: tuple[AgentRuntime, ExecutionContext],
        stopping_adapter: StoppingAdapter,
    ) -> None:
        runtime, ctx = stopping_room
        observed = await ctx.begin_delivery()
        assert observed is not None
        stopping_adapter.release.set()
        ctx.observe_platform_stop(observed)
        assert observed.observer_task is not None
        await observed.observer_task
        assert await ctx.begin_delivery() is not None

        await runtime.handle_control(control_payload(ControlMode.STOP))

        assert stopping_adapter.heard == [("room-1", ControlMode.STOP)]
