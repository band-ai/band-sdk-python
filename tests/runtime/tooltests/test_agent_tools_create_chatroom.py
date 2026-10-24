from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock

from band.runtime.tools import AgentTools


class TestAgentToolsCreateChatroom:
    """Test create_chatroom tool."""

    async def test_create_chatroom_success(self, mock_rest_client: MagicMock) -> None:
        """create_chatroom() should call REST API and return room ID."""
        mock_response = Mock()
        mock_response.data.id = "room-123"
        mock_rest_client.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_response
        )

        tools = AgentTools("room-456", mock_rest_client)
        result = await tools.create_chatroom(task_id="task-789")

        assert result == "room-123"
        mock_rest_client.agent_api_chats.create_agent_chat.assert_called_once()

    async def test_create_chatroom_without_task_id(
        self, mock_rest_client: MagicMock
    ) -> None:
        """create_chatroom() should work without task_id."""
        mock_response = Mock()
        mock_response.data.id = "room-abc"
        mock_rest_client.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_response
        )

        tools = AgentTools("room-456", mock_rest_client)
        result = await tools.create_chatroom()

        assert result == "room-abc"
