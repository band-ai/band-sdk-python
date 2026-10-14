"""AgentRuntime and RoomPresence integration."""

from __future__ import annotations

from band.runtime.runtime import AgentRuntime
from tests.runtime.agentruntime.kit import settled


class TestAgentRuntimePresenceIntegration:
    """Test integration between AgentRuntime and RoomPresence."""

    async def test_presence_callbacks_wired(self, mock_link, mock_handler):
        """RoomPresence callbacks should be wired to runtime methods."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Verify callbacks are set (not None)
        assert runtime.presence.on_room_joined is not None
        assert runtime.presence.on_room_left is not None
        assert runtime.presence.on_room_event is not None

        # Verify they work correctly by calling them
        await runtime.presence.on_room_joined("room-test", {"id": "room-test"})
        await settled(runtime, "room-test")
        assert "room-test" in runtime.executions

        await runtime.presence.on_room_left("room-test")
        assert "room-test" not in runtime.executions
