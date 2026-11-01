"""A REST-observed STOP drains captured work without widening room control."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.client.streaming import ControlMode
from band.core.turn_lifecycle import bind_turn_task
from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.tools.agent import AgentTools
from tests.runtime.runtimecontrol.kit import (
    never_executes,
)


class TestPlatformStopHook:
    async def test_rest_stop_drains_its_registered_worker_without_room_control(
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
            scope = await ctx.begin_delivery()
            assert scope is not None
            scope.admit()
            owner = bind_turn_task(
                AgentTools.from_context(ctx),
                asyncio.create_task(asyncio.Event().wait()),
            )
            ctx.observe_platform_stop(scope)
            assert scope.observer_task is not None
            await scope.observer_task
            assert owner.task.cancelled()
            assert heard == []
            assert not ctx.is_stopped
        finally:
            await ctx.stop()


import asyncio
