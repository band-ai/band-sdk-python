from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools


class TestAgentToolsRemoveParticipant:
    """Test remove_participant tool."""

    async def test_remove_participant_by_name(
        self, mock_rest_client: MagicMock
    ) -> None:
        """remove_participant() should match by name and remove via REST."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.remove_participant("User One")

        assert result["id"] == "user-1"
        assert result["name"] == "User One"
        assert result["status"] == "removed"
        mock_rest_client.agent_api_participants.remove_agent_chat_participant.assert_called_once()

    async def test_remove_participant_by_handle(
        self, mock_rest_client: MagicMock
    ) -> None:
        """remove_participant() should match by handle."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.remove_participant("user-one")

        assert result["id"] == "user-1"
        assert result["name"] == "User One"
        assert result["status"] == "removed"

    async def test_remove_participant_not_found_raises(
        self, mock_rest_client: MagicMock
    ) -> None:
        """remove_participant() should raise if not in room."""
        # Return empty participants
        mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value = MagicMock(
            data=[]
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(ValueError, match="not found in this room"):
            await tools.remove_participant("Unknown")
