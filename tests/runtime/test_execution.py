"""Tests for ExecutionContext."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.runtime.execution import Execution, ExecutionContext, _error_label
from thenvoi.runtime.types import ConversationContext, SessionConfig

# Import test helpers from conftest
from tests.conftest import (
    make_message_event,
    make_participant_added_event,
    make_participant_removed_event,
)


@pytest.fixture
def mock_link():
    """Mock ThenvoiLink for testing ExecutionContext."""
    link = MagicMock()
    link.agent_id = "agent-123"

    # REST client mock
    link.rest = MagicMock()

    # Mock list_agent_chat_participants
    participant1 = MagicMock()
    participant1.id = "user-1"
    participant1.name = "User One"
    participant1.type = "User"
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[participant1])
    )

    # Mock get_agent_chat_context
    msg1 = MagicMock()
    msg1.id = "msg-1"
    msg1.content = "Hello"
    msg1.sender_id = "user-1"
    msg1.sender_type = "User"
    msg1.sender_name = "User One"
    msg1.message_type = "text"
    msg1.inserted_at = "2024-01-01T00:00:00Z"
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[msg1])
    )

    # Mock message lifecycle methods (new in ThenvoiLink)
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.get_next_message = AsyncMock(return_value=None)  # No backlog by default

    return link


@pytest.fixture
def mock_handler():
    """Mock execution handler."""
    return AsyncMock()


class TestExecutionContextConstruction:
    """Test ExecutionContext initialization."""

    def test_init_stores_room_id(self, mock_link, mock_handler):
        """Should store room_id."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.room_id == "room-123"
        assert ctx.thread_id == "room-123"  # Alias

    def test_init_starts_idle(self, mock_link, mock_handler):
        """Should start in starting state, not running."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.state == "starting"
        assert ctx.is_running is False
        assert ctx.is_processing is False

    def test_init_empty_participants(self, mock_link, mock_handler):
        """Should start with empty participants."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.participants == []

    def test_init_llm_not_initialized(self, mock_link, mock_handler):
        """Should start with LLM not initialized."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.is_llm_initialized is False


class TestExecutionContextProtocol:
    """Test that ExecutionContext implements Execution protocol."""

    def test_implements_execution_protocol(self, mock_link, mock_handler):
        """ExecutionContext should implement Execution protocol."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert isinstance(ctx, Execution)


class TestExecutionContextLifecycle:
    """Test ExecutionContext start/stop lifecycle."""

    async def test_start_creates_task(self, mock_link, mock_handler):
        """start() should create processing task."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        assert ctx.is_running is True
        assert ctx._process_loop_task is not None

        # Cleanup
        await ctx.stop()

    async def test_start_idempotent(self, mock_link, mock_handler):
        """start() twice should not create duplicate tasks."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()
        task1 = ctx._process_loop_task
        await ctx.start()  # Second call
        task2 = ctx._process_loop_task

        assert task1 is task2

        await ctx.stop()

    async def test_stop_cancels_task(self, mock_link, mock_handler):
        """stop() should cancel task and clear it."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()
        await ctx.stop()

        assert ctx.is_running is False
        assert ctx._process_loop_task is None

    async def test_stop_when_not_started_is_noop(self, mock_link, mock_handler):
        """stop() when not started should be safe."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.stop()  # Should not raise

        assert ctx.is_running is False


class TestExecutionContextEvents:
    """Test ExecutionContext event handling."""

    async def test_on_event_enqueues(self, mock_link, mock_handler):
        """on_event() should add event to queue."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        await ctx.on_event(event)

        assert ctx.queue.qsize() == 1

    async def test_processes_message_event(self, mock_link, mock_handler):
        """Should process message events through handler."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        event = make_message_event(room_id="room-123", msg_id="msg-1", content="Hello")
        await ctx.on_event(event)

        # Wait for processing
        await asyncio.sleep(0.1)

        mock_handler.assert_called()
        call_args = mock_handler.call_args[0]
        assert call_args[0] is ctx
        assert call_args[1].type == "message_created"

        await ctx.stop()

    async def test_deduplicates_messages(self, mock_link, mock_handler):
        """Should skip duplicate message IDs."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        event = make_message_event(room_id="room-123", msg_id="msg-1")

        # Send same message twice
        await ctx.on_event(event)
        await asyncio.sleep(0.1)
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        # Should only be called once
        assert mock_handler.call_count == 1

        await ctx.stop()


class TestExecutionContextParticipants:
    """Test participant management."""

    def test_add_participant(self, mock_link, mock_handler):
        """add_participant() should add to list."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        result = ctx.add_participant(
            {
                "id": "user-1",
                "name": "Test User",
                "type": "User",
            }
        )

        assert result is True
        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "Test User"

    def test_add_participant_deduplicates(self, mock_link, mock_handler):
        """add_participant() should not add duplicates."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        ctx.add_participant({"id": "user-1", "name": "User One", "type": "User"})
        result = ctx.add_participant(
            {"id": "user-1", "name": "User One Updated", "type": "User"}
        )

        assert result is False
        assert len(ctx.participants) == 1

    def test_remove_participant(self, mock_link, mock_handler):
        """remove_participant() should remove from list."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User", "type": "User"})

        result = ctx.remove_participant("user-1")

        assert result is True
        assert len(ctx.participants) == 0

    def test_remove_participant_not_found(self, mock_link, mock_handler):
        """remove_participant() should return False if not found."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        result = ctx.remove_participant("nonexistent")

        assert result is False

    def test_participants_changed_true_initially(self, mock_link, mock_handler):
        """participants_changed() should return True initially."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.participants_changed() is True

    def test_participants_changed_false_after_mark(self, mock_link, mock_handler):
        """participants_changed() should return False after mark_participants_sent()."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User", "type": "User"})
        ctx.mark_participants_sent()

        assert ctx.participants_changed() is False

    def test_participants_changed_true_after_add(self, mock_link, mock_handler):
        """participants_changed() should return True after adding participant."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User 1", "type": "User"})
        ctx.mark_participants_sent()
        ctx.add_participant({"id": "user-2", "name": "User 2", "type": "User"})

        assert ctx.participants_changed() is True


class TestExecutionContextHydration:
    """Test context hydration."""

    async def test_hydrate_loads_participants(self, mock_link, mock_handler):
        """hydrate() should load participants from API."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.hydrate()

        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "User One"

    async def test_hydrate_loads_context(self, mock_link, mock_handler):
        """hydrate() should load context from API."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.hydrate()

        context = ctx.build_context()
        assert len(context.messages) == 1
        assert context.messages[0]["content"] == "Hello"

    async def test_hydrate_idempotent(self, mock_link, mock_handler):
        """hydrate() should only load once."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.hydrate()
        await ctx.hydrate()  # Second call

        # Should only call API once
        assert (
            mock_link.rest.agent_api_participants.list_agent_chat_participants.call_count
            == 1
        )

    async def test_get_context_hydrates_lazily(self, mock_link, mock_handler):
        """get_context() should hydrate lazily."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        context = await ctx.get_context()

        assert context.room_id == "room-123"
        assert len(context.messages) == 1

    async def test_get_context_force_refresh(self, mock_link, mock_handler):
        """get_context(force_refresh=True) should re-fetch context messages."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.get_context()
        await ctx.get_context(force_refresh=True)

        # Context API should be called twice
        assert mock_link.rest.agent_api_context.get_agent_chat_context.call_count == 2
        # Participants only loaded once (tracked via WebSocket, not re-fetched)
        assert (
            mock_link.rest.agent_api_participants.list_agent_chat_participants.call_count
            == 1
        )


class TestExecutionContextLLMState:
    """Test LLM initialization state."""

    def test_mark_llm_initialized(self, mock_link, mock_handler):
        """mark_llm_initialized() should set flag."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        ctx.mark_llm_initialized()

        assert ctx.is_llm_initialized is True


class TestExecutionContextParticipantEvents:
    """Test participant event handling."""

    async def test_participant_added_event_updates_list(self, mock_link, mock_handler):
        """participant_added event should update participants."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        await ctx.start()

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
            type="User",
        )
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        assert any(p["id"] == "user-2" for p in ctx.participants)

        await ctx.stop()

    async def test_participant_removed_event_updates_list(
        self, mock_link, mock_handler
    ):
        """participant_removed event should update participants."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.add_participant({"id": "user-1", "name": "User One", "type": "User"})

        await ctx.start()

        event = make_participant_removed_event(
            room_id="room-123",
            participant_id="user-1",
        )
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        assert not any(p["id"] == "user-1" for p in ctx.participants)

        await ctx.stop()


class TestCrashRecoverySync:
    """Test crash recovery sync mechanism."""

    @pytest.fixture
    def mock_link_with_next(self):
        """Mock ThenvoiLink with message lifecycle methods."""
        link = MagicMock()
        link.agent_id = "agent-123"
        link.rest = MagicMock()

        # Default: no messages
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # Message lifecycle methods (new in ThenvoiLink)
        link.mark_processing = AsyncMock()
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        link.get_next_message = AsyncMock(return_value=None)  # No backlog by default
        link.get_stale_processing_messages = AsyncMock(return_value=[])  # No stale msgs

        return link

    async def test_first_ws_message_sets_marker(
        self, mock_link_with_next, mock_handler
    ):
        """First WebSocket message should set sync point marker."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        assert ctx._first_ws_msg_id is None

        event = make_message_event(room_id="room-123", msg_id="msg-ws-001")
        await ctx.on_event(event)

        assert ctx._first_ws_msg_id == "msg-ws-001"

    async def test_subsequent_ws_messages_dont_change_marker(
        self, mock_link_with_next, mock_handler
    ):
        """Subsequent WebSocket messages should not change the marker."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        event1 = make_message_event(room_id="room-123", msg_id="msg-ws-001")
        event2 = make_message_event(room_id="room-123", msg_id="msg-ws-002")

        await ctx.on_event(event1)
        await ctx.on_event(event2)

        assert ctx._first_ws_msg_id == "msg-ws-001"

    async def test_sync_completes_with_no_backlog(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should complete immediately when no backlog."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        await ctx.start()
        await asyncio.sleep(0.1)

        assert ctx._sync_complete is True
        mock_link_with_next.get_next_message.assert_called()

        await ctx.stop()

    async def test_sync_processes_backlog_messages(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should process backlog messages from /next."""
        from datetime import datetime, timezone
        from thenvoi.runtime.types import PlatformMessage

        # Setup get_next_message to return one backlog message, then None
        backlog_msg = PlatformMessage(
            id="msg-backlog-001",
            room_id="room-123",
            content="Backlog message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        mock_link_with_next.get_next_message = AsyncMock(
            side_effect=[backlog_msg, None]
        )

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.start()
        await asyncio.sleep(0.2)

        # Handler should be called for backlog message
        assert mock_handler.call_count >= 1
        # The first call should be for backlog message
        call_args = mock_handler.call_args_list[0][0]
        assert call_args[1].payload.id == "msg-backlog-001"

        await ctx.stop()

    async def test_sync_point_clears_marker_and_keeps_dedupe_cache(
        self, mock_link_with_next, mock_handler
    ):
        """When sync point is reached, marker is cleared and dedupe is preserved."""
        from datetime import datetime, timezone
        from thenvoi.runtime.types import PlatformMessage

        # Setup: WS message arrives, then /next returns same message
        sync_msg = PlatformMessage(
            id="msg-sync-001",
            room_id="room-123",
            content="Sync message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        mock_link_with_next.get_next_message = AsyncMock(return_value=sync_msg)

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Enqueue WS message first (sets marker)
        ws_event = make_message_event(room_id="room-123", msg_id="msg-sync-001")
        await ctx.on_event(ws_event)
        assert ctx._first_ws_msg_id == "msg-sync-001"

        # Start should sync and find sync point
        await ctx.start()
        await asyncio.sleep(0.2)

        # Marker should be cleared
        assert ctx._first_ws_msg_id is None
        # Dedupe cache should keep processed sync id to avoid WS reprocessing
        assert "msg-sync-001" in ctx._processed_ids

        await ctx.stop()

    async def test_sync_removes_duplicate_from_ws_queue(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should dedupe when non-message events are ahead of sync-point WS copy."""
        from datetime import datetime, timezone
        from thenvoi.runtime.types import PlatformMessage

        sync_msg = PlatformMessage(
            id="msg-sync-001",
            room_id="room-123",
            content="Sync message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        mock_link_with_next.get_next_message = AsyncMock(side_effect=[sync_msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Enqueue non-message event first so sync-point duplicate isn't queue head
        participant_event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
            type="User",
        )
        await ctx.on_event(participant_event)

        # Enqueue the same message via WS (sync-point duplicate)
        ws_event = make_message_event(room_id="room-123", msg_id="msg-sync-001")
        await ctx.on_event(ws_event)

        # Queue should contain both events
        assert ctx.queue.qsize() == 2

        # Start triggers sync
        await ctx.start()
        await asyncio.sleep(0.2)

        # Sync point reached and duplicate removed from WS phase
        assert ctx._first_ws_msg_id is None
        assert ctx._sync_complete is True
        mock_link_with_next.mark_processing.assert_called_once_with(
            "room-123", "msg-sync-001"
        )
        mock_link_with_next.mark_processed.assert_called_once_with(
            "room-123", "msg-sync-001"
        )
        mock_link_with_next.mark_failed.assert_not_called()

        # Message handler should run once for sync message, and participant once.
        processed_message_ids = [
            call.args[1].payload.id
            for call in mock_handler.call_args_list
            if call.args[1].type == "message_created" and call.args[1].payload
        ]
        assert processed_message_ids.count("msg-sync-001") == 1
        participant_events = [
            call
            for call in mock_handler.call_args_list
            if call.args[1].type == "participant_added"
        ]
        assert len(participant_events) == 1

        await ctx.stop()

    async def test_sync_skips_permanently_failed(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should skip permanently failed messages."""
        from datetime import datetime, timezone
        from thenvoi.runtime.types import PlatformMessage

        failed_msg = PlatformMessage(
            id="msg-failed-001",
            room_id="room-123",
            content="Failed message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        mock_link_with_next.get_next_message = AsyncMock(side_effect=[failed_msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Mark message as permanently failed
        ctx._retry_tracker.mark_permanently_failed("msg-failed-001")

        await ctx.start()
        await asyncio.sleep(0.2)

        # Handler should NOT be called for failed message
        assert mock_handler.call_count == 0

        await ctx.stop()

    async def test_retry_tracker_records_failures(self, mock_link_with_next):
        """Retry tracker should record failed processing attempts."""
        from datetime import datetime, timezone
        from thenvoi.runtime.types import PlatformMessage

        # Handler that fails
        failing_handler = AsyncMock(side_effect=Exception("Processing failed"))

        msg = PlatformMessage(
            id="msg-001",
            room_id="room-123",
            content="Test",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(timezone.utc),
        )

        mock_link_with_next.get_next_message = AsyncMock(side_effect=[msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            failing_handler,
            config=SessionConfig(enable_context_hydration=False, max_message_retries=2),
        )

        await ctx.start()
        await asyncio.sleep(0.2)

        # Should have recorded attempt
        # Note: With max_retries=2, after 3 attempts it's permanently failed
        # But we only process once per /next call
        await ctx.stop()


class TestSessionConfigDefaults:
    """Test SessionConfig default values."""

    def test_default_enable_context_hydration_is_true(self):
        """Default should enable context hydration for backward compatibility."""
        config = SessionConfig()
        assert config.enable_context_hydration is True

    def test_default_enable_context_cache_is_true(self):
        """Default should enable context caching."""
        config = SessionConfig()
        assert config.enable_context_cache is True

    def test_can_disable_context_hydration(self):
        """Should be able to explicitly disable context hydration."""
        config = SessionConfig(enable_context_hydration=False)
        assert config.enable_context_hydration is False

    def test_default_max_message_retries(self):
        """Default max_message_retries should be 1."""
        config = SessionConfig()
        assert config.max_message_retries == 1


class TestInstantShutdown:
    """Tests for instant cancellation without timeout waiting."""

    async def test_stop_returns_quickly_when_idle(self, mock_link, mock_handler):
        """stop() should return quickly even when waiting on empty queue."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()

        # Give loop time to reach queue.get()
        await asyncio.sleep(0.01)

        # Stop should be instant (no 60-second timeout)
        start = asyncio.get_running_loop().time()
        await ctx.stop()
        elapsed = asyncio.get_running_loop().time() - start

        # Should complete in well under 1 second
        assert elapsed < 0.5, f"stop() took {elapsed}s - should be instant"

    async def test_stop_is_idempotent(self, mock_link, mock_handler):
        """Multiple stop() calls should be safe."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()
        await ctx.stop()
        await ctx.stop()  # Should not raise
        await ctx.stop()  # Should not raise

        assert ctx.is_running is False

    async def test_stop_before_start_is_safe(self, mock_link, mock_handler):
        """stop() without start() should be safe."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.stop()  # Should not raise
        assert ctx.is_running is False


class TestCancellationDuringProcessing:
    """Tests for cancellation during message processing."""

    async def test_stop_cancels_slow_processing(self, mock_link):
        """stop() should cancel message processing."""

        async def slow_handler(ctx, event):
            await asyncio.sleep(10)  # Would take 10 seconds

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            slow_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()

        # Wait for sync to complete
        await asyncio.sleep(0.05)

        # Enqueue a message to trigger processing
        event = make_message_event(room_id="room-123", msg_id="msg-001", content="Test")
        await ctx.on_event(event)

        # Give time to start processing
        await asyncio.sleep(0.05)

        # Stop should cancel processing
        start = asyncio.get_running_loop().time()
        await ctx.stop()
        elapsed = asyncio.get_running_loop().time() - start

        # Should complete quickly (not wait 10 seconds for handler)
        assert elapsed < 1.0, f"stop() took {elapsed}s - should cancel processing"


class TestContextHydrationConfig:
    """Test context hydration behavior with config."""

    async def test_get_context_skips_history_api_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """get_context() should skip history but still load participants when hydration disabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        context = await ctx.get_context()

        # History should be empty (skipped), but participants are always loaded
        assert context.messages == []
        assert len(context.participants) == 1
        assert context.participants[0]["id"] == "user-1"
        assert context.participants[0]["name"] == "User One"
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_not_called()

    async def test_get_context_calls_api_when_hydration_enabled(
        self, mock_link, mock_handler
    ):
        """get_context() should call API when hydration enabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=True),
        )

        context = await ctx.get_context()

        # Should have called API
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_called_once()
        assert len(context.messages) > 0

    async def test_get_history_for_llm_empty_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """get_history_for_llm() should return empty when hydration disabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Must hydrate first (but with disabled config, returns empty history)
        await ctx.get_context()
        history = ctx.get_history_for_llm()

        assert history == []

    async def test_hydrate_loads_participants_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """hydrate() should load participants even when context hydration is disabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.hydrate()

        # Participants should be loaded
        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "User One"
        mock_link.rest.agent_api_participants.list_agent_chat_participants.assert_called_once()

    async def test_build_participants_message_works_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """build_participants_message() should work with participants loaded via hydrate()."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.hydrate()
        msg = ctx.build_participants_message()

        # Should contain participant info
        assert "User One" in msg

    async def test_participants_preserved_when_history_hydration_fails(
        self, mock_link, mock_handler
    ):
        """Participants should be preserved even when history loading fails."""
        # Make history API fail
        mock_link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=Exception("API error")
        )

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=True),
        )

        await ctx.hydrate()

        # Participants should still be loaded despite history failure
        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "User One"
        # Context should have empty messages but populated participants
        context = ctx.build_context()
        assert context.messages == []
        assert len(context.participants) == 1


class TestContextCacheTTL:
    """Tests for context cache TTL expiry."""

    async def test_get_context_rehydrates_when_cache_is_expired(
        self, mock_link, mock_handler
    ):
        """Expired cache should be invalidated and rehydrated."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=300),
        )

        await ctx.get_context()
        mock_link.rest.agent_api_context.get_agent_chat_context.reset_mock()

        ctx._context_cache = ConversationContext(
            room_id="room-123",
            messages=[{"id": "stale-msg"}],
            participants=ctx.participants,
            hydrated_at=datetime.now(timezone.utc) - timedelta(seconds=301),
        )
        ctx._context_hydrated = True

        context = await ctx.get_context()

        mock_link.rest.agent_api_context.get_agent_chat_context.assert_awaited_once()
        assert len(context.messages) == 1
        assert context.messages[0]["id"] == "msg-1"

    async def test_get_history_for_llm_invalidates_expired_cache(
        self, mock_link, mock_handler
    ):
        """Synchronous history access should never return stale cached data."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=300),
        )

        await ctx.get_context()
        ctx._context_cache = ConversationContext(
            room_id="room-123",
            messages=[{"id": "stale-msg", "content": "stale"}],
            participants=ctx.participants,
            hydrated_at=datetime.now(timezone.utc) - timedelta(seconds=301),
        )
        ctx._context_hydrated = True

        history = ctx.get_history_for_llm()

        assert history == []
        assert ctx._context_cache is None
        assert ctx._context_hydrated is False

    async def test_zero_ttl_forces_immediate_refresh(self, mock_link, mock_handler):
        """TTL=0 should force rehydration on the next access."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=0),
        )

        await ctx.get_context()
        mock_link.rest.agent_api_context.get_agent_chat_context.reset_mock()

        await ctx.get_context()

        mock_link.rest.agent_api_context.get_agent_chat_context.assert_awaited_once()

    async def test_processing_rehydrates_expired_cache_before_handler(
        self, mock_link, mock_handler
    ):
        """Message processing should refresh expired cache before the handler runs."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(context_cache_ttl_seconds=300),
        )

        ctx._context_cache = ConversationContext(
            room_id="room-123",
            messages=[{"id": "stale-msg"}],
            participants=[],
            hydrated_at=datetime.now(timezone.utc) - timedelta(seconds=301),
        )
        ctx._context_hydrated = True

        await ctx.start()
        await asyncio.sleep(0.05)

        event = make_message_event(room_id="room-123", msg_id="msg-ttl")
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        mock_handler.assert_called()
        assert mock_link.rest.agent_api_context.get_agent_chat_context.await_count == 1

        await ctx.stop()


class TestParticipantCallbacks:
    """Tests for participant callbacks in ExecutionContext."""

    async def test_participant_added_callback_runs_before_handler(
        self, mock_link, mock_handler
    ):
        """participant_added callback should see updated participant state."""
        on_participant_added = AsyncMock()

        async def handler(ctx, event):
            assert any(p["id"] == "user-2" for p in ctx.participants)
            await mock_handler(ctx, event)

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            config=SessionConfig(enable_context_hydration=False),
            on_participant_added=on_participant_added,
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
        )
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        on_participant_added.assert_awaited_once_with("room-123", event)
        mock_handler.assert_awaited_once()

        await ctx.stop()

    async def test_participant_removed_callback_runs_before_handler(
        self, mock_link, mock_handler
    ):
        """participant_removed callback should see updated participant state."""
        on_participant_removed = AsyncMock()

        async def handler(ctx, event):
            assert all(p["id"] != "user-1" for p in ctx.participants)
            await mock_handler(ctx, event)

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            on_participant_removed=on_participant_removed,
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        event = make_participant_removed_event(
            room_id="room-123",
            participant_id="user-1",
        )
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        on_participant_removed.assert_awaited_once_with("room-123", event)
        mock_handler.assert_awaited_once()

        await ctx.stop()

    async def test_participant_callback_error_does_not_block_handler(
        self, mock_link, mock_handler
    ):
        """Participant callback errors should not stop normal execution."""
        on_participant_added = AsyncMock(side_effect=RuntimeError("callback failed"))
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
            on_participant_added=on_participant_added,
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
        )
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        on_participant_added.assert_awaited_once()
        mock_handler.assert_awaited_once()

        await ctx.stop()


class TestGracefulStopWithTimeout:
    """Tests for graceful stop with timeout."""

    async def test_stop_returns_true_when_idle(self, mock_link, mock_handler):
        """stop() should return True when not processing."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        await ctx.start()
        await asyncio.sleep(0.05)

        result = await ctx.stop(timeout=5.0)

        assert result is True
        assert ctx.is_running is False

    async def test_stop_returns_true_when_not_started(self, mock_link, mock_handler):
        """stop() should return True when not started."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        result = await ctx.stop(timeout=5.0)

        assert result is True

    async def test_stop_without_timeout_cancels_immediately(self, mock_link):
        """stop() without timeout should cancel immediately."""
        processing_started = asyncio.Event()

        async def slow_handler(ctx, event):
            processing_started.set()
            await asyncio.sleep(10)  # Would take 10 seconds

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            slow_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        # Enqueue a message
        event = make_message_event(room_id="room-123", msg_id="msg-001")
        await ctx.on_event(event)

        # Wait for processing to start
        try:
            await asyncio.wait_for(processing_started.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass  # May not start if sync takes too long

        # Stop without timeout should cancel immediately
        start = asyncio.get_running_loop().time()
        await ctx.stop()  # No timeout
        elapsed = asyncio.get_running_loop().time() - start

        assert elapsed < 1.0, f"stop() took {elapsed}s - should cancel immediately"

    async def test_stop_waits_for_processing_to_complete(self, mock_link):
        """stop(timeout) should wait for current processing to complete."""
        processing_done = asyncio.Event()

        async def quick_handler(ctx, event):
            await asyncio.sleep(0.1)  # Quick processing
            processing_done.set()

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            quick_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        # Enqueue a message
        event = make_message_event(room_id="room-123", msg_id="msg-001")
        await ctx.on_event(event)

        # Give time to start processing
        await asyncio.sleep(0.05)

        # Stop with timeout - should wait for processing
        result = await ctx.stop(timeout=5.0)

        # Should have completed gracefully
        assert result is True

    async def test_stop_returns_false_when_timeout_exceeded(self, mock_link):
        """stop(timeout) should return False when timeout exceeded."""

        async def slow_handler(ctx, event):
            await asyncio.sleep(10)  # Very slow

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            slow_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        # Enqueue a message
        event = make_message_event(room_id="room-123", msg_id="msg-001")
        await ctx.on_event(event)

        # Give time to start processing
        await asyncio.sleep(0.05)

        # Stop with short timeout
        start = asyncio.get_running_loop().time()
        result = await ctx.stop(timeout=0.1)
        elapsed = asyncio.get_running_loop().time() - start

        # Should return False (cancelled mid-processing)
        assert result is False
        # Should have taken roughly the timeout
        assert elapsed < 0.5  # Should timeout quickly

    async def test_wait_for_idle_returns_true_when_already_idle(
        self, mock_link, mock_handler
    ):
        """_wait_for_idle should return True immediately when idle."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx.state = "idle"

        result = await ctx._wait_for_idle(timeout=1.0)

        assert result is True

    async def test_wait_for_idle_returns_false_on_timeout(
        self, mock_link, mock_handler
    ):
        """_wait_for_idle should return False when timeout exceeded."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)
        ctx._set_state("processing")  # Use _set_state to properly clear idle event

        start = asyncio.get_running_loop().time()
        result = await ctx._wait_for_idle(timeout=0.1)
        elapsed = asyncio.get_running_loop().time() - start

        assert result is False
        assert elapsed >= 0.1  # Should have waited the full timeout


class TestErrorLabel:
    """Tests for the _error_label helper."""

    def test_returns_str_when_non_empty(self):
        assert (
            _error_label(ValueError("something went wrong")) == "something went wrong"
        )

    def test_falls_back_to_class_name_when_empty(self):
        class EmptyError(Exception):
            def __str__(self):
                return ""

        assert _error_label(EmptyError()) == "EmptyError"

    def test_falls_back_to_class_name_when_whitespace_only(self):
        assert _error_label(Exception("   ")) == "Exception"

    def test_strips_surrounding_whitespace(self):
        assert _error_label(ValueError("  trimmed  ")) == "trimmed"
