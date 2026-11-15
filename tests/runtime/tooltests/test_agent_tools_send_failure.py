from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from band_sdk_core import AgentFailure

from band.runtime.tools import AgentTools


class TestAgentToolsSendFailure:
    """Test send_failure's best-effort delegation over the real REST boundary."""

    async def test_send_failure_posts_an_error_event(
        self, mock_rest_client: MagicMock
    ) -> None:
        tools = AgentTools("room-123", mock_rest_client)

        await tools.send_failure(AgentFailure("codex", "boom", "timeout"))

        call_args = mock_rest_client.agent_api_events.create_agent_chat_event.call_args
        event = call_args.kwargs["event"]
        assert event.message_type == "error"
        assert event.metadata["failure"] == {
            "provider": "codex",
            "code": "timeout",
            "message": "boom",
            "detail": None,
        }

    async def test_send_failure_swallows_a_rest_rejection(
        self, mock_rest_client: MagicMock
    ) -> None:
        """A failed report must resolve, not raise -- it runs inside a
        caller's except block reporting a real provider failure already."""
        mock_rest_client.agent_api_events.create_agent_chat_event.side_effect = (
            RuntimeError("REST rejected the event")
        )
        tools = AgentTools("room-123", mock_rest_client)

        result = await tools.send_failure(AgentFailure("codex", "boom"))

        assert result == {"ok": False, "error": "REST rejected the event"}

    async def test_send_event_itself_still_raises_on_the_same_rejection(
        self, mock_rest_client: MagicMock
    ) -> None:
        """The other half of the best-effort contract: send_event's own
        raising behavior is unchanged by send_failure wrapping it."""
        mock_rest_client.agent_api_events.create_agent_chat_event.side_effect = (
            RuntimeError("REST rejected the event")
        )
        tools = AgentTools("room-123", mock_rest_client)

        with pytest.raises(RuntimeError, match="REST rejected the event"):
            await tools.send_event("task update", "task")
