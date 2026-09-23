"""Tests for the shared message/event posting choke point."""

from __future__ import annotations

import pytest

from band.client.rest import ChatEventRequest, ChatMessageRequest
from band.platform.posting import post_event, post_message
from tests.content import BLANK_CONTENT_CASES


class TestPostMessage:
    @pytest.mark.parametrize("content", BLANK_CONTENT_CASES)
    async def test_refuses_content_with_no_visible_characters(
        self, mock_rest_client, content
    ):
        result = await post_message(
            rest=mock_rest_client,
            room_id="room-123",
            request=ChatMessageRequest(content=content, mentions=[]),
        )

        assert result is None
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_called()

    async def test_sends_content_with_visible_characters(self, mock_rest_client):
        result = await post_message(
            rest=mock_rest_client,
            room_id="room-123",
            request=ChatMessageRequest(content="hello", mentions=[]),
        )

        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_called_once()
        assert (
            result
            is mock_rest_client.agent_api_messages.create_agent_chat_message.return_value.data
        )

    async def test_raises_when_the_platform_returns_no_response_data(
        self, mock_rest_client
    ):
        mock_rest_client.agent_api_messages.create_agent_chat_message.return_value.data = None

        with pytest.raises(RuntimeError, match="no response data"):
            await post_message(
                rest=mock_rest_client,
                room_id="room-123",
                request=ChatMessageRequest(content="hello", mentions=[]),
            )


class TestPostEvent:
    @pytest.mark.parametrize("content", BLANK_CONTENT_CASES)
    async def test_refuses_content_with_no_visible_characters(
        self, mock_rest_client, content
    ):
        result = await post_event(
            rest=mock_rest_client,
            room_id="room-123",
            request=ChatEventRequest(content=content, message_type="thought"),
        )

        assert result is None
        mock_rest_client.agent_api_events.create_agent_chat_event.assert_not_called()

    async def test_sends_content_with_visible_characters(self, mock_rest_client):
        result = await post_event(
            rest=mock_rest_client,
            room_id="room-123",
            request=ChatEventRequest(content="thinking", message_type="thought"),
        )

        mock_rest_client.agent_api_events.create_agent_chat_event.assert_called_once()
        assert (
            result
            is mock_rest_client.agent_api_events.create_agent_chat_event.return_value.data
        )

    async def test_leaves_content_within_the_limit_untouched(self, mock_rest_client):
        content = "x" * 16384

        await post_event(
            rest=mock_rest_client,
            room_id="room-123",
            request=ChatEventRequest(content=content, message_type="tool_result"),
        )

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        assert call_args.kwargs["event"].content == content

    async def test_truncates_oversized_content_keeping_head_and_tail(
        self, mock_rest_client
    ):
        content = "HEAD" * 10000 + "TAIL" * 10000

        await post_event(
            rest=mock_rest_client,
            room_id="room-123",
            request=ChatEventRequest(content=content, message_type="tool_result"),
        )

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        sent_content = call_args.kwargs["event"].content
        assert len(sent_content) == 16384
        assert sent_content.startswith("HEAD")
        assert sent_content.endswith("TAIL")
        assert "[truncated]" in sent_content

    async def test_raises_when_the_platform_returns_no_response_data(
        self, mock_rest_client
    ):
        mock_rest_client.agent_api_events.create_agent_chat_event.return_value.data = (
            None
        )

        with pytest.raises(RuntimeError, match="no response data"):
            await post_event(
                rest=mock_rest_client,
                room_id="room-123",
                request=ChatEventRequest(content="thinking", message_type="thought"),
            )

    async def test_does_not_truncate_a_message_of_the_same_length(
        self, mock_rest_client
    ):
        """Only events carry the platform's content cap -- messages have none."""
        content = "x" * 20000

        result = await post_message(
            rest=mock_rest_client,
            room_id="room-123",
            request=ChatMessageRequest(content=content, mentions=[]),
        )

        call_args = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.call_args
        )
        assert call_args.kwargs["message"].content == content
        assert result is not None
