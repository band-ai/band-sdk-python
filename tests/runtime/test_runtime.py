"""Tests for AgentRuntime."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.runtime.execution import ExecutionContext
from thenvoi.runtime.runtime import AgentRuntime

# Import test helpers from conftest
from tests.conftest import make_message_event, make_participant_added_event


@pytest.fixture
def mock_link():
    """Mock ThenvoiLink for testing AgentRuntime."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = False

    # Async methods
    link.connect = AsyncMock()
    link.run_forever = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()

    # REST client mock
    link.rest = MagicMock()
    link.rest.agent_api_chats = MagicMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=[])
    )

    # Message lifecycle methods
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()

    # Make link iterable for async for
    async def empty_aiter():
        return
        yield

    link.__aiter__ = lambda self: empty_aiter()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )

    return link


@pytest.fixture
def mock_handler():
    """Mock execution handler."""
    return AsyncMock()


class TestAgentRuntimeConstruction:
    """Test AgentRuntime initialization."""

    def test_init_stores_link(self, mock_link, mock_handler):
        """Should store link and agent_id."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.link is mock_link
        assert runtime.agent_id == "agent-123"

    def test_init_creates_presence(self, mock_link, mock_handler):
        """Should create RoomPresence."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.presence is not None
        assert runtime.presence.link is mock_link

    def test_init_empty_executions(self, mock_link, mock_handler):
        """Should start with no executions."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.executions == {}
        assert runtime.active_sessions == {}

    def test_init_with_room_filter(self, mock_link, mock_handler):
        """Should accept room filter."""

        def my_filter(room):
            return room.get("type") == "task"

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, room_filter=my_filter
        )

        assert runtime.presence.room_filter is my_filter


class TestAgentRuntimeLifecycle:
    """Test AgentRuntime start/stop lifecycle."""

    async def test_start_starts_presence(self, mock_link, mock_handler):
        """start() should start RoomPresence."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Mock presence.start to track it was called
        runtime.presence.start = AsyncMock()

        await runtime.start()

        runtime.presence.start.assert_called_once()

    async def test_stop_stops_all_executions(self, mock_link, mock_handler):
        """stop() should stop all execution contexts."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Create some executions
        await runtime._create_execution("room-1")
        await runtime._create_execution("room-2")

        assert len(runtime.executions) == 2

        await runtime.stop()

        assert len(runtime.executions) == 0

    async def test_stop_calls_cleanup_callback(self, mock_link, mock_handler):
        """stop() should call on_session_cleanup for each room."""
        cleanup_rooms = []

        async def on_cleanup(room_id):
            cleanup_rooms.append(room_id)

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, on_session_cleanup=on_cleanup
        )

        await runtime._create_execution("room-1")
        await runtime._create_execution("room-2")

        await runtime.stop()

        assert set(cleanup_rooms) == {"room-1", "room-2"}


class TestAgentRuntimeExecutionManagement:
    """Test execution context management."""

    async def test_creates_execution_on_room_joined(self, mock_link, mock_handler):
        """Room joined should create execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime._on_room_joined("room-123", {"id": "room-123"})

        assert "room-123" in runtime.executions
        assert isinstance(runtime.executions["room-123"], ExecutionContext)

        # Cleanup
        await runtime.stop()

    async def test_destroys_execution_on_room_left(self, mock_link, mock_handler):
        """Room left should destroy execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime._create_execution("room-123")
        await runtime._on_room_left("room-123")

        assert "room-123" not in runtime.executions

    async def test_execution_idempotent(self, mock_link, mock_handler):
        """Creating execution twice should return same instance."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = await runtime._create_execution("room-123")
        exec2 = await runtime._create_execution("room-123")

        assert exec1 is exec2

        await runtime.stop()

    async def test_active_sessions_returns_copy(self, mock_link, mock_handler):
        """active_sessions should return a copy."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime._create_execution("room-123")
        sessions = runtime.active_sessions

        # Modifying returned dict shouldn't affect internal state
        sessions["room-999"] = MagicMock()

        assert "room-999" not in runtime.executions

        await runtime.stop()


class TestAgentRuntimeEventRouting:
    """Test event routing to executions."""

    async def test_routes_events_to_execution(self, mock_link, mock_handler):
        """Room events should be routed to execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime._create_execution("room-123")

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

        await runtime._create_execution("room-123")

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
        )
        await runtime._on_room_event("room-123", event)
        await asyncio.sleep(0.1)

        on_participant_added.assert_awaited_once()

        await runtime.stop()


class TestAgentRuntimeCustomFactory:
    """Test custom execution factory."""

    async def test_uses_custom_factory(self, mock_link, mock_handler):
        """Should use custom execution factory when provided."""
        custom_execution = MagicMock()
        custom_execution.start = AsyncMock()
        custom_execution.stop = AsyncMock()

        def my_factory(room_id, link):
            custom_execution.room_id = room_id
            return custom_execution

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, execution_factory=my_factory
        )

        await runtime._create_execution("room-123")

        assert runtime.executions["room-123"] is custom_execution
        assert custom_execution.room_id == "room-123"
        custom_execution.start.assert_called_once()

        await runtime.stop()


class TestAgentRuntimePresenceIntegration:
    """Test integration between AgentRuntime and RoomPresence."""

    async def test_presence_callbacks_wired(self, mock_link, mock_handler):
        """RoomPresence callbacks should be wired to runtime methods."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Verify callbacks are set (not None)
        assert runtime.presence.on_room_joined is not None
        assert runtime.presence.on_room_left is not None
        assert runtime.presence.on_room_event is not None

        # Verify they work correctly by calling them
        await runtime.presence.on_room_joined("room-test", {"id": "room-test"})
        assert "room-test" in runtime.executions

        await runtime.presence.on_room_left("room-test")
        assert "room-test" not in runtime.executions


class TestAgentRuntimeRun:
    """Test AgentRuntime.run() method."""

    async def test_run_starts_and_runs_forever(self, mock_link, mock_handler):
        """run() should start and call link.run_forever()."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Mock start to track it was called
        runtime.start = AsyncMock()
        runtime.stop = AsyncMock()

        await runtime.run()

        runtime.start.assert_called_once()
        mock_link.run_forever.assert_called_once()
        runtime.stop.assert_called_once()

    async def test_run_stops_on_error(self, mock_link, mock_handler):
        """run() should stop even if run_forever raises."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        mock_link.run_forever.side_effect = Exception("Connection lost")
        runtime.start = AsyncMock()
        runtime.stop = AsyncMock()

        with pytest.raises(Exception, match="Connection lost"):
            await runtime.run()

        runtime.stop.assert_called_once()
