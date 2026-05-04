"""Tests for ThenvoiLink."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.platform.link import ThenvoiLink


@pytest.fixture
def mock_ws_client():
    """Mock WebSocketClient for testing ThenvoiLink."""
    ws = AsyncMock()

    # Async context manager support
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)

    # Mock channel operations
    ws.join_chat_room_channel = AsyncMock()
    ws.leave_chat_room_channel = AsyncMock()
    ws.join_agent_rooms_channel = AsyncMock()
    ws.join_room_participants_channel = AsyncMock()
    ws.leave_room_participants_channel = AsyncMock()
    ws.run_forever = AsyncMock()

    return ws


@pytest.fixture
def mock_rest_client():
    """Mock AsyncRestClient for testing ThenvoiLink."""
    client = AsyncMock()
    client.agent_api_identity = MagicMock()
    client.agent_api_messages = MagicMock()
    client.agent_api_chats = MagicMock()
    client.agent_api_peers = MagicMock()
    client.agent_api_participants = MagicMock()
    client.agent_api_events = MagicMock()
    client.agent_api_context = MagicMock()
    return client


class TestThenvoiLinkConstruction:
    """Test ThenvoiLink initialization."""

    def test_init_stores_credentials(self):
        """Should store agent_id, api_key, and URLs."""
        link = ThenvoiLink(
            agent_id="agent-123",
            api_key="test-key",
            ws_url="wss://test.com/ws",
            rest_url="https://test.com",
        )

        assert link.agent_id == "agent-123"
        assert link.api_key == "test-key"
        assert link.ws_url == "wss://test.com/ws"
        assert link.rest_url == "https://test.com"

    def test_init_creates_rest_client(self):
        """Should create AsyncRestClient exposed as .rest."""
        link = ThenvoiLink(
            agent_id="agent-123",
            api_key="test-key",
        )

        assert link.rest is not None

    def test_init_starts_disconnected(self):
        """Should start in disconnected state."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        assert link.is_connected is False
        assert link._ws is None
        assert link._subscribed_rooms == set()

    def test_init_empty_event_queue(self):
        """Should start with empty event queue."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        assert link._event_queue.empty()


class TestThenvoiLinkConnection:
    """Test connection lifecycle."""

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_connect_creates_websocket(self, mock_ws_class, mock_ws_client):
        """connect() should create WebSocketClient and enter context."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        mock_ws_class.assert_called_once_with(
            link.ws_url,
            link.api_key,
            link.agent_id,
            on_reconnect=link._on_reconnected,
            on_disconnect=link._on_disconnected,
        )
        mock_ws_client.__aenter__.assert_called_once()
        assert link.is_connected is True

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_connect_when_already_connected_logs_warning(
        self, mock_ws_class, mock_ws_client
    ):
        """connect() when already connected should log warning and return."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.connect()  # Second call

        # Should only create WS once
        assert mock_ws_class.call_count == 1

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_disconnect_exits_websocket_context(
        self, mock_ws_class, mock_ws_client
    ):
        """disconnect() should exit WebSocket context."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.disconnect()

        mock_ws_client.__aexit__.assert_called_once_with(None, None, None)
        assert link.is_connected is False
        assert link._ws is None

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_disconnect_clears_subscribed_rooms(
        self, mock_ws_class, mock_ws_client
    ):
        """disconnect() should clear tracked subscriptions."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        link._subscribed_rooms.add("room-1")
        link._subscribed_rooms.add("room-2")

        await link.disconnect()

        assert link._subscribed_rooms == set()

    async def test_reconnect_keeps_tracked_room_subscriptions(self):
        """_on_reconnected() should preserve room tracking for PHX re-subscriptions."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        link._subscribed_rooms.update({"room-1", "room-2"})

        await link._on_reconnected()

        assert link._subscribed_rooms == {"room-1", "room-2"}

    async def test_disconnect_when_not_connected_is_noop(self):
        """disconnect() when not connected should be a no-op."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.disconnect()  # Should not raise

        assert link.is_connected is False

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_run_forever_delegates_to_websocket(
        self, mock_ws_class, mock_ws_client
    ):
        """run_forever() should delegate to WebSocket."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.run_forever()

        mock_ws_client.run_forever.assert_called_once()

    async def test_run_forever_raises_when_not_connected(self):
        """run_forever() should raise RuntimeError when not connected."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.run_forever()


class TestThenvoiLinkSubscriptions:
    """Test subscription management."""

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_subscribe_agent_rooms_joins_channel(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_agent_rooms() should join agent rooms channel."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_rooms("agent-123")

        mock_ws_client.join_agent_rooms_channel.assert_called_once()
        # Verify callbacks were passed
        call_kwargs = mock_ws_client.join_agent_rooms_channel.call_args[1]
        assert "on_room_added" in call_kwargs
        assert "on_room_removed" in call_kwargs

    async def test_subscribe_agent_rooms_raises_when_not_connected(self):
        """subscribe_agent_rooms() should raise when not connected."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.subscribe_agent_rooms("agent-123")

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_subscribe_room_joins_channels(self, mock_ws_class, mock_ws_client):
        """subscribe_room() should join chat room and participants channels."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        mock_ws_client.join_chat_room_channel.assert_called_once()
        mock_ws_client.join_room_participants_channel.assert_called_once()

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_subscribe_room_tracks_subscription(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_room() should track room in _subscribed_rooms."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        assert "room-123" in link._subscribed_rooms

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_subscribe_room_idempotent(self, mock_ws_class, mock_ws_client):
        """subscribe_room() twice should not re-subscribe."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.subscribe_room("room-123")  # Second call

        # Should only join once
        assert mock_ws_client.join_chat_room_channel.call_count == 1

    async def test_subscribe_room_raises_when_not_connected(self):
        """subscribe_room() should raise when not connected."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.subscribe_room("room-123")

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_unsubscribe_room_leaves_channels(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should leave both channels."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.unsubscribe_room("room-123")

        mock_ws_client.leave_chat_room_channel.assert_called_once_with("room-123")
        mock_ws_client.leave_room_participants_channel.assert_called_once_with(
            "room-123"
        )

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_unsubscribe_room_removes_from_tracking(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should remove room from _subscribed_rooms."""
        mock_ws_class.return_value = mock_ws_client

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.unsubscribe_room("room-123")

        assert "room-123" not in link._subscribed_rooms

    @patch("thenvoi.platform.link.WebSocketClient")
    async def test_unsubscribe_room_handles_leave_errors(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should handle errors gracefully."""
        mock_ws_class.return_value = mock_ws_client
        mock_ws_client.leave_chat_room_channel.side_effect = Exception("Leave failed")

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        link._subscribed_rooms.add("room-123")

        # Should not raise, just log warning
        await link.unsubscribe_room("room-123")

        # Room should still be removed from tracking
        assert "room-123" not in link._subscribed_rooms

    async def test_unsubscribe_room_noop_when_not_subscribed(self):
        """unsubscribe_room() should be no-op for unsubscribed room."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        # Should not raise
        await link.unsubscribe_room("room-123")


class TestThenvoiLinkEventQueue:
    """Test event queue mechanism (async iterator pattern)."""

    def test_queue_event_adds_to_queue(self):
        """_queue_event() should add event to queue."""
        from tests.conftest import make_message_event

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        link._queue_event(event)

        assert link._event_queue.qsize() == 1

    async def test_async_iteration_gets_events(self):
        """async for should yield events from queue."""
        from tests.conftest import make_message_event

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        link._queue_event(event)

        # Get event via async iteration
        received = await link.__anext__()
        assert received is event

    def test_queue_drops_when_full(self):
        """Queue should drop events when full (no blocking)."""
        from tests.conftest import make_message_event

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        # Fill the queue (maxsize=1000)
        for i in range(1000):
            link._queue_event(make_message_event(msg_id=f"msg-{i}"))

        # Queue should be full
        assert link._event_queue.full()

        # Adding one more should not block (drops or handles gracefully)
        # Note: Exact behavior depends on implementation


class TestThenvoiLinkEventHandlers:
    """Test internal event handlers that queue typed events."""

    async def test_on_room_added_queues_room_added_event(self):
        """_on_room_added() should queue RoomAddedEvent."""
        from thenvoi.platform.event import RoomAddedEvent

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        # Create mock payload
        payload = MagicMock()
        payload.id = "room-123"
        payload.model_dump.return_value = {
            "id": "room-123",
            "title": "Test Room",
            "owner": {"id": "u1", "name": "User", "type": "User"},
            "status": "active",
            "type": "direct",
            "created_at": "2024-01-01T00:00:00Z",
            "participant_role": "member",
        }

        await link._on_room_added(payload)

        # Check event was queued
        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomAddedEvent)
        assert event.room_id == "room-123"

    async def test_on_room_removed_queues_room_removed_event(self):
        """_on_room_removed() should queue RoomRemovedEvent."""
        from thenvoi.platform.event import RoomRemovedEvent

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        payload = MagicMock()
        payload.id = "room-123"
        payload.model_dump.return_value = {
            "id": "room-123",
            "status": "removed",
            "type": "direct",
            "title": "Test Room",
            "removed_at": "2024-01-01T00:00:00Z",
        }

        await link._on_room_removed(payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomRemovedEvent)
        assert event.room_id == "room-123"

    async def test_on_message_created_queues_message_event(self):
        """_on_message_created() should queue MessageEvent."""
        from thenvoi.platform.event import MessageEvent

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        payload = MagicMock()
        payload.id = "msg-123"
        payload.content = "Hello"
        payload.sender_id = "user-456"
        payload.sender_type = "User"
        payload.chat_room_id = "room-123"
        payload.message_type = "text"
        payload.inserted_at = "2024-01-01T00:00:00Z"
        payload.updated_at = "2024-01-01T00:00:00Z"
        payload.metadata = MagicMock()
        payload.metadata.mentions = []
        payload.metadata.status = "sent"

        await link._on_message_created("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, MessageEvent)
        assert event.room_id == "room-123"
        assert event.payload.content == "Hello"

    async def test_on_participant_added_queues_participant_added_event(self):
        """_on_participant_added() should queue ParticipantAddedEvent."""
        from thenvoi.client.streaming import ParticipantAddedPayload
        from thenvoi.platform.event import ParticipantAddedEvent

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        payload = ParticipantAddedPayload(id="user-123", name="Test User", type="User")

        await link._on_participant_added("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, ParticipantAddedEvent)
        assert event.room_id == "room-123"
        assert event.payload.id == "user-123"

    async def test_on_participant_removed_queues_participant_removed_event(self):
        """_on_participant_removed() should queue ParticipantRemovedEvent."""
        from thenvoi.client.streaming import ParticipantRemovedPayload
        from thenvoi.platform.event import ParticipantRemovedEvent

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        payload = ParticipantRemovedPayload(id="user-123")

        await link._on_participant_removed("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, ParticipantRemovedEvent)
        assert event.room_id == "room-123"

    async def test_on_room_deleted_queues_room_deleted_event(self):
        """_on_room_deleted() should queue RoomDeletedEvent."""
        from thenvoi.client.streaming import RoomDeletedPayload
        from thenvoi.platform.event import RoomDeletedEvent

        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")

        payload = RoomDeletedPayload(id="room-123")

        await link._on_room_deleted("room-123", payload)

        assert link._event_queue.qsize() == 1
        event = await link._event_queue.get()
        assert isinstance(event, RoomDeletedEvent)
        assert event.room_id == "room-123"
        assert event.payload.id == "room-123"


class TestMarkFailed:
    """Tests for mark_failed error normalization."""

    @pytest.mark.asyncio
    async def test_replaces_empty_error_with_unknown(self):
        """mark_failed should replace empty error string with 'Unknown error'."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock()

        await link.mark_failed("room-1", "msg-1", "")

        link.rest.agent_api_messages.mark_agent_message_failed.assert_called_once()
        call_kwargs = link.rest.agent_api_messages.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "Unknown error"

    @pytest.mark.asyncio
    async def test_replaces_whitespace_error_with_unknown(self):
        """mark_failed should replace whitespace-only error with 'Unknown error'."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock()

        await link.mark_failed("room-1", "msg-1", "   ")

        call_kwargs = link.rest.agent_api_messages.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "Unknown error"

    @pytest.mark.asyncio
    async def test_passes_through_non_empty_error(self):
        """mark_failed should pass through a valid error string as-is."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.mark_agent_message_failed = AsyncMock()

        await link.mark_failed("room-1", "msg-1", "connection reset")

        call_kwargs = link.rest.agent_api_messages.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "connection reset"


class TestGetStaleProcessingMessages:
    """Tests for stale processing recovery pagination."""

    @pytest.mark.asyncio
    async def test_paginates_across_all_pages(self):
        """get_stale_processing_messages should fetch every result page."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()

        msg_1 = MagicMock()
        msg_1.id = "msg-1"
        msg_1.chat_room_id = "room-1"
        msg_1.content = "first"
        msg_1.sender_id = "user-1"
        msg_1.sender_type = "User"
        msg_1.sender_name = "User One"
        msg_1.message_type = "text"
        msg_1.metadata = {}
        msg_1.inserted_at = None

        msg_2 = MagicMock()
        msg_2.id = "msg-2"
        msg_2.chat_room_id = "room-1"
        msg_2.content = "second"
        msg_2.sender_id = "user-2"
        msg_2.sender_type = "User"
        msg_2.sender_name = "User Two"
        msg_2.message_type = "text"
        msg_2.metadata = {}
        msg_2.inserted_at = None

        response_page_1 = MagicMock()
        response_page_1.data = [msg_1]
        response_page_1.metadata = MagicMock(page=1, total_pages=2)

        response_page_2 = MagicMock()
        response_page_2.data = [msg_2]
        response_page_2.metadata = MagicMock(page=2, total_pages=2)

        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            side_effect=[response_page_1, response_page_2]
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert [message.id for message in messages] == ["msg-1", "msg-2"]
        assert link.rest.agent_api_messages.list_agent_messages.await_count == 2
        first_call = link.rest.agent_api_messages.list_agent_messages.await_args_list[0]
        second_call = link.rest.agent_api_messages.list_agent_messages.await_args_list[
            1
        ]
        assert first_call.kwargs["page"] == 1
        assert second_call.kwargs["page"] == 2

    @pytest.mark.asyncio
    async def test_stops_after_first_page_when_total_pages_missing(self):
        """Missing pagination metadata should safely return the first page."""
        link = ThenvoiLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()

        msg = MagicMock()
        msg.id = "msg-1"
        msg.chat_room_id = "room-1"
        msg.content = "first"
        msg.sender_id = "user-1"
        msg.sender_type = "User"
        msg.sender_name = "User One"
        msg.message_type = "text"
        msg.metadata = {}
        msg.inserted_at = None

        response_page_1 = MagicMock()
        response_page_1.data = [msg]
        response_page_1.metadata = MagicMock(total_pages=None)

        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            return_value=response_page_1
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert [message.id for message in messages] == ["msg-1"]
        link.rest.agent_api_messages.list_agent_messages.assert_awaited_once()
