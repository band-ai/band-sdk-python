"""AgentRuntime custom execution factory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig


class TestAgentRuntimeCustomFactory:
    """Test custom execution factory."""

    async def test_uses_custom_factory(self, mock_link, mock_handler):
        """Should use custom execution factory when provided."""
        custom_execution = MagicMock()
        custom_execution.start = AsyncMock()
        custom_execution.stop = AsyncMock()

        def my_factory(room_id, link):
            custom_execution.room_id = room_id
            return custom_execution

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, execution_factory=my_factory
        )

        await runtime.rooms.admit("room-123")

        assert runtime.executions["room-123"] is custom_execution
        assert custom_execution.room_id == "room-123"
        custom_execution.start.assert_called_once()

        await runtime.stop()

    async def test_factory_built_context_receives_execution_created_hook(
        self, mock_link, mock_handler
    ) -> None:
        bound: list[ExecutionContext] = []

        def on_created(ctx: ExecutionContext) -> None:
            bound.append(ctx)

        def my_factory(room_id, link, *, hub_room_id=None):
            return ExecutionContext(
                room_id,
                link,
                on_execute=mock_handler,
                config=SessionConfig(enable_context_hydration=False),
                agent_id="agent-123",
            )

        runtime = AgentRuntime(
            mock_link,
            "agent-123",
            mock_handler,
            execution_factory=my_factory,
            on_execution_created=on_created,
        )
        await runtime.rooms.admit("room-123")
        assert bound == [runtime.executions["room-123"]]
        await runtime.stop()
