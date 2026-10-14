"""Reconnect probes preserve a stopped room when /next returns 204."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.platform.event import ReconnectedEvent
from band.runtime.execution import ExecutionContext
from band.runtime.types import PlatformMessage


class TestStopSurvivesReconnect:
    async def test_reconnect_while_stopped_does_not_invoke_adapter(self):
        """Reconnect probes preserve a stopped room when /next returns 204."""
        link = MagicMock()
        link.agent_id = "agent-123"
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.mark_processing = AsyncMock(return_value=True)
        link.mark_processed = AsyncMock(return_value=True)
        link.mark_failed = AsyncMock(return_value=True)
        # Listing must not become an alternate execution path.
        stuck = PlatformMessage(
            id="stuck",
            room_id="room-123",
            content="hi",
            sender_id="u1",
            sender_type="User",
            sender_name="U1",
            message_type="text",
            metadata={},
            created_at=None,
        )
        link.get_stale_processing_messages = AsyncMock(return_value=[stuck])
        link.get_next_message = AsyncMock(return_value=None)  # /next gate: 204

        executed: list[str] = []

        async def on_execute(ctx, event):
            executed.append(event.payload.id)

        ctx = ExecutionContext("room-123", link, on_execute, agent_id="agent-123")
        ctx._stopped = True

        # Drive the reconnect sync path directly.
        await ctx._process_event(ReconnectedEvent())

        assert executed == []  # adapter never invoked while stopped
        link.mark_processing.assert_not_awaited()
        # Diagnostic listing is never used to dispatch execution.
        link.get_stale_processing_messages.assert_not_awaited()
        link.get_next_message.assert_awaited_once_with("room-123")
        assert ctx.is_stopped
