from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.scheduler import ResyncOutcome
from band.runtime.sessionconfig import SessionConfig
from tests.sessionsync.kit import (
    make_message,
)


class TestCrashRecovery:
    """Unfinished messages recover through authoritative /next selection."""

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
        ctx._is_running = True
        ctx._handler_mock = handler
        return ctx

    @pytest.mark.asyncio
    async def test_recovers_single_stale_message(self, ctx, mock_link):
        """Should process a single stale message from crash recovery."""
        stale_msg = make_message("stale-001")
        mock_link.get_next_message.side_effect = [stale_msg, None]

        assert await ctx.scheduler.run_pass(recovery=True) is ResyncOutcome.WORK

        # Handler should be called once for the stale message
        assert ctx._handler_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_recovers_multiple_stale_messages(self, ctx, mock_link):
        """Should process all stale messages in order."""
        stale_msgs = [
            make_message("stale-001"),
            make_message("stale-002"),
            make_message("stale-003"),
        ]
        mock_link.get_next_message.side_effect = [*stale_msgs, None]

        assert await ctx.scheduler.run_pass(recovery=True) is ResyncOutcome.WORK

        assert ctx._handler_mock.call_count == 3

    @pytest.mark.asyncio
    async def test_no_stale_messages_is_noop(self, ctx, mock_link):
        """Should do nothing when no stale messages exist."""
        mock_link.get_next_message.return_value = None

        assert await ctx.scheduler.run_pass(recovery=True) is ResyncOutcome.EMPTY

        ctx._handler_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_next_gate_does_not_fall_back_to_processing_list(
        self, ctx, mock_link
    ):
        """An empty /next never dispatches unfinished work from a listing."""
        mock_link.get_next_message.return_value = None
        mock_link.get_stale_processing_messages.return_value = [
            make_message("unfinished")
        ]

        await ctx.scheduler.run_pass(recovery=True)

        ctx._handler_mock.assert_not_awaited()
        mock_link.get_stale_processing_messages.assert_not_awaited()
        mock_link.get_next_message.assert_awaited_once()
