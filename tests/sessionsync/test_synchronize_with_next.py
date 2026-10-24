from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_message_event,
    make_participant_added_event,
)
from tests.runtime.scheduler.driver import attempt_delivery
from tests.sessionsync.kit import (
    make_message,
)


class TestSynchronizeWithNext:
    """Tests for _synchronize_with_next() using marker."""

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
    async def test_sync_completes_when_next_returns_none(self, ctx, mock_link):
        """Sync should complete when /next returns None (no backlog)."""
        mock_link.get_next_message.return_value = None

        await ctx.scheduler.run_pass(recovery=True)

        mock_link.get_next_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_processes_backlog_messages(self, ctx, mock_link):
        """Sync should process messages from /next until sync point."""
        backlog_msg = make_message("backlog-001")

        # Return backlog message, then None
        mock_link.get_next_message.side_effect = [backlog_msg, None]

        await ctx.scheduler.run_pass(recovery=True)

        # Handler should be called for backlog message
        assert ctx._handler_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_sync_point_reached_clears_marker_and_keeps_cache(
        self, ctx, mock_link
    ):
        """When sync point reached, marker clears but dedupe cache is retained."""
        # First process a backlog message (populates LRU cache)
        backlog_msg = make_message("backlog-001")
        sync_msg = make_message("sync-001")

        # Enqueue sync message via WebSocket
        sync_event = make_message_event(msg_id="sync-001")
        await ctx.on_event(sync_event)
        ctx.scheduler.absorb()
        assert ctx.scheduler.first_ws_id == "sync-001"

        # /next returns backlog first, then sync message
        mock_link.get_next_message.side_effect = [backlog_msg, sync_msg]

        await ctx.scheduler.run_pass(recovery=True)

        # Marker should be cleared
        assert ctx.scheduler.first_ws_id is None
        # Queue should be empty (duplicate removed)
        assert ctx.queue.empty()
        # Dedupe cache keeps processed IDs to avoid immediate WS reprocessing
        assert "sync-001" in ctx.claims.completed_ids(ctx.room_id)

    @pytest.mark.asyncio
    async def test_sync_point_with_non_message_head_processes_once(
        self, ctx, mock_link
    ):
        """Sync-point message should execute once even when WS queue head is not a message."""
        sync_msg = make_message("sync-001")
        mock_link.get_next_message.side_effect = [sync_msg, None]

        participant_event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
            type="User",
        )
        await ctx.on_event(participant_event)
        await ctx.on_event(make_message_event(msg_id="sync-001"))

        await ctx.scheduler.run_pass(recovery=True)

        # Process remaining WS events (participant only after dedupe drain)
        while not ctx.queue.empty():
            await attempt_delivery(ctx, ctx.queue.get_nowait())

        mock_link.mark_processed.assert_called_once_with("room-123", "sync-001")
        mock_link.mark_failed.assert_not_called()

        processed_message_ids = [
            call.args[1].payload.id
            for call in ctx._handler_mock.call_args_list
            if call.args[1].type == "message_created" and call.args[1].payload
        ]
        assert processed_message_ids.count("sync-001") == 1

    @pytest.mark.asyncio
    async def test_sync_skips_permanently_failed(self, ctx, mock_link):
        """Sync should skip permanently failed messages."""
        failed_msg = make_message("failed-001")
        ctx._retry_tracker.mark_permanently_failed("failed-001")

        mock_link.get_next_message.return_value = failed_msg

        await ctx.scheduler.run_pass(recovery=True)

        # Handler should NOT be called
        ctx._handler_mock.assert_not_called()
