"""Tests for idle-timeout resync and reconnect resync (INT-333).

Covers:
- request_resync() enqueues _ResyncRequest sentinel
- Sentinel wakes Phase 2 loop and calls _resync_pending_messages()
- Idle timeout calls _resync_pending_messages() after configured seconds
- _resync_pending_messages() happy path: processes missed message
- _resync_pending_messages() empty path: /next returns None, no error
- AgentRuntime._on_reconnected() calls request_resync() on all executions
- AgentRuntime._on_reconnected() skips executions without request_resync (custom impls)
- RoomPresence._handle_reconnect() fires on_reconnected even when auto_subscribe_existing=False
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.runtime.execution import ExecutionContext, _ResyncRequest
from thenvoi.runtime.presence import RoomPresence
from thenvoi.runtime.runtime import AgentRuntime
from thenvoi.runtime.types import PlatformMessage, SessionConfig


async def wait_for_condition(
    predicate, *, timeout: float = 1.0, interval: float = 0.01
) -> None:
    """Wait until a predicate becomes true, failing fast on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    pytest.fail("Timed out waiting for condition")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_link():
    """ThenvoiLink mock configured for ExecutionContext tests."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = False

    link.connect = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()

    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_chats = MagicMock()
    api_response = MagicMock()
    api_response.data = []
    api_response.metadata = MagicMock()
    api_response.metadata.total_pages = None
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(return_value=api_response)

    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])

    async def empty_aiter():
        return
        yield

    link.__aiter__ = lambda self: empty_aiter()

    return link


@pytest.fixture
def mock_handler():
    return AsyncMock()


def make_platform_message(
    msg_id: str = "msg-1", room_id: str = "room-1"
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content="Hello",
        sender_id="user-999",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata={},
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# TestRequestResync
# ---------------------------------------------------------------------------


class TestRequestResync:
    """Tests for ExecutionContext.request_resync()."""

    async def test_enqueues_resync_sentinel(self, mock_link, mock_handler):
        """request_resync() should put a _ResyncRequest onto the queue."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)

        await ctx.request_resync()

        assert ctx.queue.qsize() == 1
        item = ctx.queue.get_nowait()
        assert isinstance(item, _ResyncRequest)

    async def test_sentinel_triggers_resync(self, mock_link, mock_handler):
        """Enqueueing a sentinel should cause the Phase 2 loop to call /next."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()

        await wait_for_condition(lambda: ctx._sync_complete)

        call_count_before = mock_link.get_next_message.call_count

        await ctx.request_resync()
        await wait_for_condition(
            lambda: mock_link.get_next_message.call_count > call_count_before
        )

        # At least one more /next call should have occurred
        assert mock_link.get_next_message.call_count > call_count_before

        await ctx.stop()

    async def test_multiple_resyncs_dont_crash(self, mock_link, mock_handler):
        """Multiple rapid request_resync() calls should not crash or deadlock."""
        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        for _ in range(5):
            await ctx.request_resync()

        await wait_for_condition(lambda: ctx.queue.qsize() == 0)
        # Still running, no exception
        assert ctx.is_running

        await ctx.stop()


# ---------------------------------------------------------------------------
# TestIdleTimeout
# ---------------------------------------------------------------------------


class TestIdleTimeout:
    """Tests for idle-timeout resync in Phase 2 loop."""

    async def test_idle_timeout_triggers_resync(self, mock_link, mock_handler):
        """Phase 2 should call /next after idle_resync_seconds with no WS events."""
        config = SessionConfig(idle_resync_seconds=0.01)  # fast timeout for test
        ctx = ExecutionContext("room-1", mock_link, mock_handler, config=config)
        await ctx.start()

        await wait_for_condition(
            lambda: mock_link.get_next_message.call_count >= 2,
            timeout=1.0,
        )

        # get_next_message is called during Phase 1 AND during idle timeout resync
        assert mock_link.get_next_message.call_count >= 2

        await ctx.stop()

    async def test_idle_timeout_does_not_fire_when_events_arrive(
        self, mock_link, mock_handler
    ):
        """If events arrive before timeout, resync should not add extra /next calls."""
        from tests.conftest import make_message_event

        config = SessionConfig(idle_resync_seconds=60)  # very long timeout
        ctx = ExecutionContext("room-1", mock_link, mock_handler, config=config)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        call_count_after_phase1 = mock_link.get_next_message.call_count

        # Send a real WS event — this resets the idle timer
        event = make_message_event(room_id="room-1", msg_id="msg-x")
        await ctx.on_event(event)
        await wait_for_condition(lambda: mock_handler.await_count >= 1)

        # No idle timeout should have fired (timeout is 60s)
        assert mock_link.get_next_message.call_count == call_count_after_phase1

        await ctx.stop()


# ---------------------------------------------------------------------------
# TestResyncPendingMessages
# ---------------------------------------------------------------------------


class TestResyncPendingMessages:
    """Tests for ExecutionContext._resync_pending_messages()."""

    async def test_empty_returns_immediately(self, mock_link, mock_handler):
        """/next returning None immediately should not call the handler."""
        mock_link.get_next_message.return_value = None

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Call directly without starting the loop
        await ctx._resync_pending_messages()

        mock_handler.assert_not_called()

    async def test_processes_single_missed_message(self, mock_link, mock_handler):
        """/next returning one message then None should process that message."""
        msg = make_platform_message(msg_id="missed-1", room_id="room-1")
        # First call returns the missed message, second returns None
        mock_link.get_next_message.side_effect = [msg, None]

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        # Reset state so _resync_pending_messages runs fresh
        mock_link.get_next_message.side_effect = [msg, None]
        await ctx._resync_pending_messages()

        # Handler should have been called for the missed message
        mock_handler.assert_called()

        await ctx.stop()

    async def test_skips_duplicate_message(self, mock_link, mock_handler):
        """A message whose ID is already in _processed_ids should be skipped."""
        msg = make_platform_message(msg_id="dup-1", room_id="room-1")
        mock_link.get_next_message.side_effect = [msg, None]

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Mark the message as already processed
        ctx._processed_ids["dup-1"] = True

        await ctx._resync_pending_messages()

        mock_handler.assert_not_called()

    async def test_exception_does_not_propagate(self, mock_link, mock_handler):
        """Exceptions inside _resync_pending_messages should be caught, not raised."""
        mock_link.get_next_message.side_effect = RuntimeError("API down")

        ctx = ExecutionContext("room-1", mock_link, mock_handler)
        # Should complete without raising
        await ctx._resync_pending_messages()


# ---------------------------------------------------------------------------
# TestAgentRuntimeOnReconnected
# ---------------------------------------------------------------------------


class TestAgentRuntimeOnReconnected:
    """Tests for AgentRuntime._on_reconnected()."""

    async def test_calls_request_resync_on_all_executions(
        self, mock_link, mock_handler
    ):
        """_on_reconnected() should call request_resync() on each execution."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = MagicMock()
        exec1.request_resync = AsyncMock()
        exec2 = MagicMock()
        exec2.request_resync = AsyncMock()

        runtime.executions = {"room-1": exec1, "room-2": exec2}

        await runtime._on_reconnected()

        exec1.request_resync.assert_called_once()
        exec2.request_resync.assert_called_once()

    async def test_skips_execution_without_request_resync(
        self, mock_link, mock_handler
    ):
        """_on_reconnected() should not raise if an execution lacks request_resync."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Simulate a legacy/custom Execution without the new method
        legacy_exec = MagicMock(spec=[])  # spec=[] → no attributes at all

        runtime.executions = {"room-legacy": legacy_exec}

        # Should not raise AttributeError
        await runtime._on_reconnected()

    async def test_one_failure_does_not_abort_others(self, mock_link, mock_handler):
        """A failing request_resync() should not prevent the others from running."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = MagicMock()
        exec1.request_resync = AsyncMock(side_effect=RuntimeError("boom"))
        exec2 = MagicMock()
        exec2.request_resync = AsyncMock()

        runtime.executions = {"room-1": exec1, "room-2": exec2}

        await runtime._on_reconnected()

        exec2.request_resync.assert_called_once()

    async def test_presence_reconnect_still_resyncs_active_executions_if_api_fails(
        self, mock_link, mock_handler
    ):
        """The full presence -> runtime callback chain should survive API failure."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        execution = MagicMock()
        execution.request_resync = AsyncMock()
        runtime.executions = {"room-1": execution}

        mock_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        await runtime.presence._handle_reconnect()

        execution.request_resync.assert_called_once()


# ---------------------------------------------------------------------------
# TestPresenceReconnectOnReconnectedCallback
# ---------------------------------------------------------------------------


class TestPresenceReconnectOnReconnectedCallback:
    """Tests that on_reconnected fires reliably from _handle_reconnect."""

    @pytest.fixture
    def mock_presence_link(self):
        link = MagicMock()
        link.agent_id = "agent-123"
        link.is_connected = False
        link.connect = AsyncMock()
        link.subscribe_agent_rooms = AsyncMock()
        link.subscribe_room = AsyncMock()
        link.unsubscribe_room = AsyncMock()

        link.rest = MagicMock()
        link.rest.agent_api_chats = MagicMock()

        # Return a properly structured response so _list_existing_rooms terminates
        # correctly (total_pages=None breaks the pagination loop after one call).
        api_response = MagicMock()
        api_response.data = []
        api_response.metadata = MagicMock()
        api_response.metadata.total_pages = None
        link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=api_response
        )

        async def empty_aiter():
            return
            yield

        link.__aiter__ = lambda self: empty_aiter()
        return link

    async def test_on_reconnected_fires_with_auto_subscribe_true(
        self, mock_presence_link
    ):
        """on_reconnected fires after reconnect when auto_subscribe_existing=True."""
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_fires_with_auto_subscribe_false(
        self, mock_presence_link
    ):
        """on_reconnected fires after reconnect even when auto_subscribe_existing=False.

        The finally block ensures the callback always fires regardless of early
        returns in the try block (which exits early when auto_subscribe_existing=False).
        """
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=False)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_still_fires_if_api_fails(self, mock_presence_link):
        """on_reconnected should still fire if room reconciliation fails.

        Existing executions still need a /next resync even when the room-list API
        is temporarily unavailable during reconnect.
        """
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        mock_presence_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_still_fires_if_unsubscribe_fails(
        self, mock_presence_link
    ):
        """on_reconnected should still fire if a stale room unsubscribe fails."""
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        room = MagicMock()
        room.id = "room-new"
        room.model_dump.return_value = {"id": "room-new"}

        api_response = MagicMock()
        api_response.data = [room]
        api_response.metadata = MagicMock()
        api_response.metadata.total_pages = None
        mock_presence_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=api_response
        )
        mock_presence_link.unsubscribe_room = AsyncMock(
            side_effect=RuntimeError("unsubscribe failed")
        )

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        presence.rooms = {"room-old"}
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_cancelled_error_propagates_from_callback(self, mock_presence_link):
        """CancelledError raised in on_reconnected must propagate (structured concurrency)."""

        async def on_reconnected_that_raises():
            raise asyncio.CancelledError

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=False)
        presence.on_reconnected = on_reconnected_that_raises

        with pytest.raises(asyncio.CancelledError):
            await presence._handle_reconnect()
