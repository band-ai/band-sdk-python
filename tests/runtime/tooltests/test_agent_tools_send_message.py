from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.core.exceptions import BandToolError
from band.runtime.tools import AgentTools
from tests.content import BLANK_CONTENT_CASES


class TestAgentToolsSendMessage:
    """Test send_message tool."""

    async def test_send_message_success(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """send_message() should send via REST and return the Fern model."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_message("Hello!", mentions=["User One"])

        # Now returns Fern model (mock), not dict
        assert result.model_dump()["id"] == "msg-123"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()

    async def test_send_message_resolves_mentions(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """send_message() should resolve mention names to IDs and handles."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        await tools.send_message("Hello @User One!", mentions=["User One"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert len(message.mentions) == 1
        assert message.mentions[0].id == "user-1"
        assert message.mentions[0].handle == "@user-one"

    async def test_send_message_accepts_participant_id(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """An exact ID routes to its participant without knowing the handle."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        await tools.send_message("Hello!", mentions=["user-1"])

        call = mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        assert call.kwargs["message"].mentions[0].id == "user-1"

    async def test_send_message_omits_attachment_ids_when_not_given(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """Without attachment_ids, the field must be unset, not explicitly None.

        ChatMessageRequest serializes with exclude_unset=True; passing
        attachment_ids=None would still mark the field "set" and send a
        literal JSON null, which the platform rejects (expects an array or
        an absent key) -- reproduced live against a real deployment.
        """
        tools = AgentTools("room-123", mock_rest_client, participants)

        await tools.send_message("Hello!", mentions=["User One"])

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        message = call_args.kwargs["message"]
        assert "attachment_ids" not in message.model_fields_set

    async def test_send_message_empty_mentions_excludes_self(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """The empty-mentions error lists other participants but not the agent
        itself — an agent can't @mention itself."""

        tools = AgentTools(
            "room-123", mock_rest_client, participants, agent_id="user-2"
        )

        with pytest.raises(BandToolError) as exc_info:
            await tools.send_message("Hello!", mentions=[])

        message = str(exc_info.value)
        assert "@user-one" in message
        assert "@user-two" not in message

    async def test_send_message_unknown_mention_raises(
        self, mock_rest_client: MagicMock, participants: list[dict[str, str]]
    ) -> None:
        """send_message() should raise for unknown mention."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        with pytest.raises(ValueError, match="Unknown participant 'Unknown'"):
            await tools.send_message("Hello!", mentions=["Unknown"])

    @pytest.mark.parametrize("content", BLANK_CONTENT_CASES)
    async def test_send_message_refuses_content_with_no_visible_characters(
        self,
        mock_rest_client: MagicMock,
        participants: list[dict[str, str]],
        content: str,
    ) -> None:
        """send_message() should never call the API with blank content."""
        tools = AgentTools("room-123", mock_rest_client, participants)

        result = await tools.send_message(content, mentions=["User One"])

        assert result is None
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_called()
