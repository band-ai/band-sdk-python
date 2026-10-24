from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.core.exceptions import BandToolError
from band.runtime.tools import AgentTools


class TestEmptyMentionsValidation:
    """Test that empty mentions return a helpful error with participant names."""

    async def test_raises_error_with_participant_names(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should raise BandToolError listing available participants when mentions empty."""

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(
            BandToolError, match="At least one mention is required"
        ) as exc_info:
            await tools.send_message("Hello!", mentions=[])

        assert "@user-one" in str(exc_info.value)
        assert "@user-two" in str(exc_info.value)
        # Should NOT have called the API
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_called()

    async def test_raises_error_when_mentions_none(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should raise BandToolError when mentions is None."""

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="At least one mention is required"):
            await tools.send_message("Hello!", mentions=None)

    async def test_uses_handle_when_available(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should prefer handle over name in error message."""

        participants = [
            {"id": "user-1", "name": "User One", "type": "User", "handle": "@user-one"},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="@user-one"):
            await tools.send_message("Hello!", mentions=[])

    async def test_omits_participant_without_handle(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Should omit handle-less participants — they can't be @mentioned."""

        participants = [
            {"id": "user-1", "name": "User One", "type": "User"},
        ]
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(
            BandToolError, match="At least one mention is required"
        ) as exc_info:
            await tools.send_message("Hello!", mentions=[])

        # Name must not be offered as a mention target — only real handles are.
        assert "User One" not in str(exc_info.value)
        # With no mentionable handles there is nothing to suggest, so the error
        # carries no handle list rather than an empty one.
        assert "Available handles:" not in str(exc_info.value)

    async def test_no_error_when_mentions_provided(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Should proceed normally when mentions are provided."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_message("Hello!", mentions=["User One"])

        # Now returns Fern model; verify it has the expected attribute
        assert result.model_dump()["id"] == "msg-123"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    async def test_execute_tool_call_raises_band_tool_error(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """execute_tool_call lets BandToolError propagate for wrapper translation."""

        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(BandToolError, match="At least one mention is required"):
            await tools.execute_tool_call(
                "band_send_message", {"content": "Hello!", "mentions": []}
            )
