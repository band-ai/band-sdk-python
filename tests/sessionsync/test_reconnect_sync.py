from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.platform.event import ReconnectedEvent
from band.runtime.execution import ExecutionContext
from band.runtime.scheduler import ResyncRequest
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.scheduler.driver import attempt_delivery
from tests.sessionsync.kit import (
    make_message,
)


class TestReconnectSync:
    """Tests for reconnect-triggered resynchronization."""

    @pytest.fixture
    def ctx(self, mock_link):
        """Create ExecutionContext with mocked dependencies."""
        handler = AsyncMock()
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        ctx._handler_mock = handler
        return ctx

    @pytest.mark.asyncio
    async def test_reconnected_event_is_enqueued_for_serialized_sync(self, ctx):
        """Reconnect events should be serialized through the room queue."""
        await ctx.on_event(ReconnectedEvent())

        queued = ctx.queue.get_nowait()
        assert isinstance(queued, ResyncRequest)
        ctx._handler_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reconnected_event_runs_sync_when_processed(self, ctx, mock_link):
        """Queued reconnect events should run sync inside the execution loop."""
        mock_link.get_next_message.side_effect = [make_message("reconnected"), None]

        await attempt_delivery(ctx, ReconnectedEvent())

        assert ctx.claims.completed_ids(ctx.room_id) == ["reconnected"]
        ctx._handler_mock.assert_awaited_once()
