from __future__ import annotations

from unittest.mock import MagicMock

from band.runtime.tools import AgentTools


class TestAgentToolsFromContext:
    """Test AgentTools.from_context() factory method."""

    def test_from_context_creates_tools(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """from_context() should create AgentTools from ExecutionContext."""
        # Mock ExecutionContext
        mock_ctx = MagicMock()
        mock_ctx.room_id = "room-456"
        mock_ctx.link = MagicMock()
        mock_ctx.link.rest = mock_rest_client
        mock_ctx.participants = participants

        tools = AgentTools.from_context(mock_ctx)

        assert tools.room_id == "room-456"
        assert tools.rest is mock_rest_client
        assert tools._participants == participants
