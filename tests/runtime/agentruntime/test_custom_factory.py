"""AgentRuntime custom execution factory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.runtime.runtime import AgentRuntime


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
