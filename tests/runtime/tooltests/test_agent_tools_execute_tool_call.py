from __future__ import annotations

from unittest.mock import MagicMock

from band.runtime.tools import AgentTools


class TestAgentToolsExecuteToolCall:
    """Test execute_tool_call dispatch."""

    async def test_execute_send_message(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """execute_tool_call() should dispatch band_send_message."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.execute_tool_call(
            "band_send_message", {"content": "Hello!", "mentions": ["User One"]}
        )

        assert result["id"] == "msg-123"

    async def test_execute_send_event(self, mock_rest_client: MagicMock) -> None:
        """execute_tool_call() should dispatch band_send_event."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call(
            "band_send_event", {"content": "Thinking...", "message_type": "thought"}
        )

        assert result["message_type"] == "thought"

    async def test_execute_lookup_peers(self, mock_rest_client: MagicMock) -> None:
        """execute_tool_call() should dispatch band_lookup_peers."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("band_lookup_peers", {"page": 1})

        # execute_tool_call calls .model_dump() on the Fern response
        assert isinstance(result, dict)

    async def test_execute_get_participants(self, mock_rest_client: MagicMock) -> None:
        """execute_tool_call() should dispatch band_get_participants."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("band_get_participants", {})

        assert isinstance(result, list)
        assert result[0]["id"] == "user-1"
        assert result[0]["name"] == "User One"

    async def test_execute_unknown_tool(self, mock_rest_client: MagicMock) -> None:
        """execute_tool_call() should return error for unknown tool."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.execute_tool_call("unknown_tool", {})

        assert "Unknown tool" in result

    async def test_execute_validation_error(self, mock_rest_client: MagicMock) -> None:
        """execute_tool_call() should return validation error in LLM-friendly format."""
        tools = AgentTools("room-123", mock_rest_client)

        # Missing required field
        result = await tools.execute_tool_call(
            "band_send_message", {"content": "Hello"}
        )

        assert "Invalid arguments for band_send_message" in result
        assert "mentions" in result  # Should mention the missing field

    async def test_execute_runtime_error(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """execute_tool_call() should return execution error."""
        mock_rest_client.agent_api_messages.create_agent_chat_message.side_effect = (
            Exception("Network error")
        )
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.execute_tool_call(
            "band_send_message", {"content": "Hello!", "mentions": ["User One"]}
        )

        assert "Error executing" in result
