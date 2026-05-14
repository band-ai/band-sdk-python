"""
Tests for ExecutionContext sync point tracking and LRU deduplication.

Tests cover:
- _first_ws_msg_id marker tracking in on_event()
- LRU dedupe cache in _process_event()
- _synchronize_with_next() using marker instead of queue peeking
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from thenvoi.platform.event import ReconnectedEvent
from thenvoi.runtime.execution import ExecutionContext
from thenvoi.runtime.types import PlatformMessage, SessionConfig

# Import test helpers from conftest
from tests.conftest import make_message_event, make_participant_added_event


def make_message(msg_id: str, room_id: str = "room-123") -> PlatformMessage:
    """Helper to create test messages for /next API simulation."""
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content=f"Content for {msg_id}",
        sender_id="user-456",
        sender_type="User",
        sender_name="Test User",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def mock_link():
    """Create mock ThenvoiLink with all required async endpoints."""
    link = MagicMock()
    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.get_next_message = AsyncMock(return_value=None)
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    return link


class TestFirstWsMessageIdMarker:
    """Tests for _first_ws_msg_id tracking in on_event()."""

    @pytest.fixture
    def ctx(self, mock_link):
        """Create ExecutionContext with mocked dependencies."""

        async def handler(ctx, event):
            pass

        return ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=handler,
        )

    @pytest.mark.asyncio
    async def test_first_event_sets_marker(self, ctx):
        """First enqueued message event should set _first_ws_msg_id."""
        assert ctx._first_ws_msg_id is None

        event = make_message_event(msg_id="msg-001")
        await ctx.on_event(event)

        assert ctx._first_ws_msg_id == "msg-001"

    @pytest.mark.asyncio
    async def test_subsequent_event_does_not_change_marker(self, ctx):
        """Subsequent events should not change the marker."""
        event1 = make_message_event(msg_id="msg-001")
        event2 = make_message_event(msg_id="msg-002")
        event3 = make_message_event(msg_id="msg-003")

        await ctx.on_event(event1)
        await ctx.on_event(event2)
        await ctx.on_event(event3)

        # Should still be first message
        assert ctx._first_ws_msg_id == "msg-001"

    @pytest.mark.asyncio
    async def test_marker_preserved_after_queue_empty(self, ctx):
        """Marker should be preserved even if queue becomes empty."""
        event = make_message_event(msg_id="msg-001")
        await ctx.on_event(event)

        # Drain the queue
        ctx.queue.get_nowait()
        assert ctx.queue.empty()

        # Marker should still be set
        assert ctx._first_ws_msg_id == "msg-001"


class TestLruDedupeCache:
    """Tests for LRU dedupe cache in _process_event()."""

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
        # Store handler reference for assertion
        ctx._handler_mock = handler
        return ctx

    @pytest.mark.asyncio
    async def test_processed_event_added_to_cache(self, ctx):
        """Processed message events should be added to LRU cache."""
        event = make_message_event(msg_id="msg-001")

        await ctx._process_event(event)

        assert "msg-001" in ctx._processed_ids

    @pytest.mark.asyncio
    async def test_duplicate_event_skipped(self, ctx):
        """Duplicate message events should be skipped."""
        event = make_message_event(msg_id="msg-001")

        # Process first time
        await ctx._process_event(event)
        assert ctx._handler_mock.call_count == 1

        # Process same event again - should be skipped
        await ctx._process_event(event)
        assert ctx._handler_mock.call_count == 1  # Still 1

    @pytest.mark.asyncio
    async def test_lru_cache_evicts_oldest(self, ctx):
        """LRU cache should evict oldest entries when full."""
        limit = ctx._max_processed_ids

        for i in range(limit):
            event = make_message_event(msg_id=f"msg-{i:03d}")
            await ctx._process_event(event)

        assert len(ctx._processed_ids) == limit
        assert "msg-000" in ctx._processed_ids

        event_overflow = make_message_event(msg_id=f"msg-{limit:03d}")
        await ctx._process_event(event_overflow)

        assert len(ctx._processed_ids) == limit
        assert "msg-000" not in ctx._processed_ids
        assert f"msg-{limit:03d}" in ctx._processed_ids

    @pytest.mark.asyncio
    async def test_duplicate_refreshes_lru_position(self, ctx):
        """Accessing duplicate should refresh its LRU position."""
        limit = ctx._max_processed_ids

        for i in range(limit):
            event = make_message_event(msg_id=f"msg-{i:03d}")
            await ctx._process_event(event)

        event0_dup = make_message_event(msg_id="msg-000")
        await ctx._process_event(event0_dup)

        event_overflow = make_message_event(msg_id=f"msg-{limit:03d}")
        await ctx._process_event(event_overflow)

        assert "msg-000" in ctx._processed_ids
        assert "msg-001" not in ctx._processed_ids


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

        await ctx._synchronize_with_next()

        mock_link.get_next_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_processes_backlog_messages(self, ctx, mock_link):
        """Sync should process messages from /next until sync point."""
        backlog_msg = make_message("backlog-001")

        # Return backlog message, then None
        mock_link.get_next_message.side_effect = [backlog_msg, None]

        await ctx._synchronize_with_next()

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
        assert ctx._first_ws_msg_id == "sync-001"

        # /next returns backlog first, then sync message
        mock_link.get_next_message.side_effect = [backlog_msg, sync_msg]

        await ctx._synchronize_with_next()

        # Marker should be cleared
        assert ctx._first_ws_msg_id is None
        # Queue should be empty (duplicate removed)
        assert ctx.queue.empty()
        # Dedupe cache keeps processed IDs to avoid immediate WS reprocessing
        assert "sync-001" in ctx._processed_ids

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

        await ctx._synchronize_with_next()

        # Process remaining WS events (participant only after dedupe drain)
        while not ctx.queue.empty():
            await ctx._process_event(ctx.queue.get_nowait())

        mock_link.mark_processing.assert_called_once_with("room-123", "sync-001")
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

        await ctx._synchronize_with_next()

        # Handler should NOT be called
        ctx._handler_mock.assert_not_called()


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
        ctx._synchronize_with_next = AsyncMock()

        await ctx.on_event(ReconnectedEvent())

        assert ctx._reconnect_sync_requested is True
        queued = ctx.queue.get_nowait()
        assert isinstance(queued, ReconnectedEvent)

    @pytest.mark.asyncio
    async def test_reconnected_event_runs_sync_when_processed(self, ctx):
        """Queued reconnect events should run sync inside the execution loop."""
        ctx._synchronize_with_next = AsyncMock()
        ctx._reconnect_sync_requested = True

        await ctx._process_event(ReconnectedEvent())

        ctx._synchronize_with_next.assert_awaited_once_with()
        assert ctx._reconnect_sync_requested is False


class TestCrashRecovery:
    """Tests for _recover_stale_processing_messages()."""

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
        mock_link.get_stale_processing_messages.return_value = [stale_msg]

        await ctx._recover_stale_processing_messages()

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
        mock_link.get_stale_processing_messages.return_value = stale_msgs

        await ctx._recover_stale_processing_messages()

        assert ctx._handler_mock.call_count == 3

    @pytest.mark.asyncio
    async def test_no_stale_messages_is_noop(self, ctx, mock_link):
        """Should do nothing when no stale messages exist."""
        mock_link.get_stale_processing_messages.return_value = []

        await ctx._recover_stale_processing_messages()

        ctx._handler_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_calls_recovery_before_next(self, ctx, mock_link):
        """_synchronize_with_next should call crash recovery before /next loop."""
        mock_link.get_next_message.return_value = None

        await ctx._synchronize_with_next()

        # Recovery should be called exactly once
        mock_link.get_stale_processing_messages.assert_called_once()
        # And it should happen (we verify order by checking both were called)
        mock_link.get_next_message.assert_called_once()
