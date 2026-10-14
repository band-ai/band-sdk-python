"""AgentRuntime event routing to executions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from band.runtime.runtime import AgentRuntime
from tests.conftest import make_message_event, make_participant_added_event


class TestAgentRuntimeEventRouting:
    """Test event routing to executions."""

    async def test_routes_events_to_execution(self, mock_link, mock_handler):
        """Room events should be routed to execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime.rooms.admit("room-123")

        event = make_message_event(
            room_id="room-123",
            msg_id="msg-1",
            content="Hello",
        )
        await runtime._on_room_event("room-123", event)

        # Event should be in execution's queue
        assert runtime.executions["room-123"].queue.qsize() == 1

        await runtime.stop()

    async def test_ignores_events_for_unknown_room(self, mock_link, mock_handler):
        """Events for unknown rooms should be ignored."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        event = make_message_event(
            room_id="unknown-room",
            msg_id="msg-1",
        )

        # Should not raise
        await runtime._on_room_event("unknown-room", event)

    async def test_routes_participant_callbacks_to_execution(
        self, mock_link, mock_handler
    ):
        """Participant callbacks should fire through the execution context."""
        on_participant_added = AsyncMock()
        runtime = AgentRuntime(
            mock_link,
            "agent-123",
            mock_handler,
            on_participant_added=on_participant_added,
        )

        await runtime.rooms.admit("room-123")

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
        )
        await runtime._on_room_event("room-123", event)
        await asyncio.sleep(0.1)

        on_participant_added.assert_awaited_once()

        await runtime.stop()
