"""A REST-observed stop reaches the adapter's hook without latching the room."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.client.streaming import ControlMode
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from tests.runtime.runtimecontrol.kit import (
    never_executes,
)


class TestPlatformStopHook:
    """A REST-observed stop reaches the adapter's hook without latching the room."""

    async def test_rest_stop_uses_default_runtime_adapter_hook_without_latching(
        self,
    ) -> None:
        link = MagicMock()
        link.get_next_message = AsyncMock(return_value=None)
        heard: list[tuple[str, ControlMode]] = []

        async def on_control(room_id: str, mode: ControlMode) -> None:
            heard.append((room_id, mode))

        runtime = AgentRuntime(
            link=link,
            agent_id="agent-123",
            on_execute=never_executes,
            on_control=on_control,
        )
        ctx = await runtime.rooms.admit("room-1")
        assert isinstance(ctx, ExecutionContext)
        try:
            async with ctx._turn_scope() as scope:
                assert scope is not None
                ctx.observe_platform_stop(scope)
                assert scope.observer_task is not None
                await scope.observer_task
                assert heard == [("room-1", ControlMode.STOP)]
                assert not ctx.is_stopped
        finally:
            await ctx.stop()
