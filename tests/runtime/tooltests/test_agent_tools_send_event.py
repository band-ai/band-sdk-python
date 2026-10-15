from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest.errors import ForbiddenError
from band_rest.types import Error, ErrorError

from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.execution import ExecutionContext
from band.runtime.tools import AgentTools
from tests.content import BLANK_CONTENT_CASES


class TestAgentToolsSendEvent:
    """Test send_event tool."""

    async def test_send_event_success(self, mock_rest_client: MagicMock) -> None:
        """send_event() should send via REST and return the Fern model."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.send_event("Thinking...", "thought")

        # Now returns Fern model (mock), not dict
        assert result.model_dump()["message_type"] == "thought"
        mock_rest_client.agent_api_events.create_agent_chat_event.assert_called_once()

    async def test_send_event_with_metadata(self, mock_rest_client: MagicMock) -> None:
        """send_event() should pass metadata."""
        tools = AgentTools("room-123", mock_rest_client)

        await tools.send_event("Error!", "error", metadata={"code": 500})

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        event = call_args.kwargs["event"]
        assert event.metadata == {"code": 500}

    async def test_send_event_within_limit_untouched(
        self, mock_rest_client: MagicMock
    ) -> None:
        """send_event() should pass short content through unchanged."""
        tools = AgentTools("room-123", mock_rest_client)
        content = "x" * 16384

        await tools.send_event(content, "tool_result")

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        assert call_args.kwargs["event"].content == content

    async def test_send_event_truncates_oversized_content(
        self, mock_rest_client: MagicMock
    ) -> None:
        """send_event() caps oversized content, keeping both its head and tail."""
        tools = AgentTools("room-123", mock_rest_client)
        content = "HEAD" * 10000 + "TAIL" * 10000

        await tools.send_event(content, "tool_result")

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        sent_content = call_args.kwargs["event"].content
        assert len(sent_content) == 16384
        assert sent_content.startswith("HEAD")  # head is retained
        assert sent_content.endswith("TAIL")  # and the tail isn't dropped
        assert "[truncated]" in sent_content  # with a marker between them

    @pytest.mark.parametrize("content", BLANK_CONTENT_CASES)
    async def test_send_event_refuses_content_with_no_visible_characters(
        self, mock_rest_client: MagicMock, content: str
    ) -> None:
        """send_event() should never call the API with blank content — the platform 422s on it."""
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.send_event(content, "tool_result")

        assert result is None
        mock_rest_client.agent_api_events.create_agent_chat_event.assert_not_called()

    async def test_stopped_scope_cancels_posts_without_latching_room(
        self, mock_rest_client: MagicMock
    ) -> None:
        ctx = ExecutionContext(
            "room-123", MagicMock(rest=mock_rest_client), AsyncMock()
        )
        await ctx.begin_delivery()
        tools = AgentTools.from_context(ctx)
        mock_rest_client.agent_api_events.create_agent_chat_event.side_effect = (
            ForbiddenError(
                body=Error(
                    error=ErrorError(
                        code="forbidden",
                        message="Agent execution is stopped; cannot post events",
                        request_id="req-1",
                    )
                )
            )
        )
        for content in ("first", "later"):
            with pytest.raises(asyncio.CancelledError):
                await tools.send_event(content, "thought")
        assert not ctx.is_stopped
        assert not tools.turn.complete
        mock_rest_client.agent_api_events.create_agent_chat_event.assert_awaited_once()

    async def test_send_event_without_an_execution_still_raises_the_typed_error(
        self, mock_rest_client: MagicMock
    ) -> None:
        """Tools built without an execution context have no room state to
        adopt: the typed error propagates so the caller sees the platform's
        rejection."""
        mock_rest_client.agent_api_events.create_agent_chat_event.side_effect = (
            ForbiddenError(
                body=Error(
                    error=ErrorError(
                        code="forbidden",
                        message="Agent execution is stopped; cannot post events",
                        request_id="req-1",
                    )
                )
            )
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RoomExecutionStoppedError):
            await tools.send_event("thinking", "thought")
