"""AgentRuntime start/stop lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock

from band.runtime.runtime import AgentRuntime


class TestAgentRuntimeLifecycle:
    """Test AgentRuntime start/stop lifecycle."""

    async def test_start_starts_presence(self, mock_link, mock_handler):
        """start() should start RoomPresence."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Mock presence.start to track it was called
        runtime.presence.start = AsyncMock()

        await runtime.start()

        runtime.presence.start.assert_called_once()

    async def test_stop_stops_all_executions(self, mock_link, mock_handler):
        """stop() should stop all execution contexts."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Create some executions
        await runtime.rooms.admit("room-1")
        await runtime.rooms.admit("room-2")

        assert len(runtime.executions) == 2

        await runtime.stop()

        assert len(runtime.executions) == 0

    async def test_stop_calls_cleanup_callback(self, mock_link, mock_handler):
        """stop() should call on_session_cleanup for each room."""
        cleanup_rooms = []

        async def on_cleanup(room_id):
            cleanup_rooms.append(room_id)

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, on_session_cleanup=on_cleanup
        )

        await runtime.rooms.admit("room-1")
        await runtime.rooms.admit("room-2")

        await runtime.stop()

        assert set(cleanup_rooms) == {"room-1", "room-2"}
