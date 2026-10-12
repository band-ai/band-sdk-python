"""AgentRuntime construction."""

from __future__ import annotations

from band.runtime.runtime import AgentRuntime


class TestAgentRuntimeConstruction:
    """Test AgentRuntime initialization."""

    def test_init_stores_link(self, mock_link, mock_handler):
        """Should store link and agent_id."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.link is mock_link
        assert runtime.agent_id == "agent-123"

    def test_init_creates_presence(self, mock_link, mock_handler):
        """Should create RoomPresence."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.presence is not None
        assert runtime.presence.link is mock_link

    def test_init_empty_executions(self, mock_link, mock_handler):
        """Should start with no executions."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.executions == {}
        assert runtime.active_sessions == {}

    def test_init_with_room_filter(self, mock_link, mock_handler):
        """Should accept room filter."""

        def my_filter(room):
            return room.get("type") == "task"

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, room_filter=my_filter
        )

        assert runtime.presence.room_filter is my_filter
