"""Tests for RoomPresence."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.runtime.presence import RoomPresence

# Import test helpers from conftest
from thenvoi.platform.event import ReconnectedEvent

from tests.conftest import (
    make_message_event,
    make_room_added_event,
    make_room_deleted_event,
    make_room_removed_event,
)


@pytest.fixture
def mock_link():
    """Mock ThenvoiLink for testing RoomPresence."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = False

    # Async methods
    link.connect = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()

    # REST client mock
    link.rest = MagicMock()
    link.rest.agent_api_chats = MagicMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=[])
    )

    # Make link iterable for async for (returns empty iterator by default)
    async def empty_aiter():
        return
        yield  # Make it a generator

    link.__aiter__ = lambda self: empty_aiter()

    return link


class TestRoomPresenceConstruction:
    """Test RoomPresence initialization."""

    def test_init_stores_link(self, mock_link):
        """Should store link reference."""
        presence = RoomPresence(mock_link)

        assert presence.link is mock_link
        assert presence.rooms == set()

    def test_init_with_room_filter(self, mock_link):
        """Should accept room filter."""

        def my_filter(room):
            return room.get("type") == "task"

        presence = RoomPresence(mock_link, room_filter=my_filter)

        assert presence.room_filter is my_filter

    def test_init_callbacks_none(self, mock_link):
        """Should start with no callbacks."""
        presence = RoomPresence(mock_link)

        assert presence.on_room_joined is None
        assert presence.on_room_left is None
        assert presence.on_room_event is None


class TestRoomPresenceStart:
    """Test RoomPresence.start()."""

    async def test_start_creates_event_task(self, mock_link):
        """start() should create internal event consumer task."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)

        await presence.start()

        assert presence._event_task is not None

        await presence.stop()

    async def test_start_connects_if_not_connected(self, mock_link):
        """start() should connect link if not already connected."""
        mock_link.is_connected = False
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)

        await presence.start()

        mock_link.connect.assert_called_once()

    async def test_start_skips_connect_if_connected(self, mock_link):
        """start() should not reconnect if already connected."""
        mock_link.is_connected = True
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)

        await presence.start()

        mock_link.connect.assert_not_called()

    async def test_start_subscribes_to_agent_rooms(self, mock_link):
        """start() should subscribe to agent rooms channel."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)

        await presence.start()

        mock_link.subscribe_agent_rooms.assert_called_once_with("agent-123")

    async def test_start_subscribes_existing_rooms(self, mock_link):
        """start() should subscribe to existing rooms by default."""
        # Mock existing rooms
        room1 = MagicMock()
        room1.id = "room-1"
        room1.model_dump.return_value = {"id": "room-1"}
        room2 = MagicMock()
        room2.id = "room-2"
        room2.model_dump.return_value = {"id": "room-2"}
        mock_link.rest.agent_api_chats.list_agent_chats.return_value = MagicMock(
            data=[room1, room2],
            metadata=MagicMock(total_pages=1),
        )

        presence = RoomPresence(mock_link, auto_subscribe_existing=True)
        await presence.start()

        assert "room-1" in presence.rooms
        assert "room-2" in presence.rooms
        assert mock_link.subscribe_room.call_count == 2


class TestRoomPresenceStop:
    """Test RoomPresence.stop()."""

    async def test_stop_clears_rooms(self, mock_link):
        """stop() should clear tracked rooms."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        presence.rooms.add("room-1")
        presence.rooms.add("room-2")

        await presence.stop()

        assert presence.rooms == set()

    async def test_stop_calls_on_room_left(self, mock_link):
        """stop() should call on_room_left for each room."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        presence.rooms.add("room-1")
        presence.rooms.add("room-2")

        left_rooms = []

        async def on_left(room_id):
            left_rooms.append(room_id)

        presence.on_room_left = on_left

        await presence.stop()

        assert set(left_rooms) == {"room-1", "room-2"}


class TestRoomPresenceRoomAdded:
    """Test room_added event handling."""

    async def test_room_added_subscribes_to_room(self, mock_link):
        """room_added should subscribe to room channels."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()

        event = make_room_added_event(room_id="room-123", title="Test Room")
        await presence._handle_room_added(event)

        mock_link.subscribe_room.assert_called_with("room-123")

        await presence.stop()

    async def test_room_added_tracks_room(self, mock_link):
        """room_added should track room in presence.rooms."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()

        event = make_room_added_event(room_id="room-123")
        await presence._handle_room_added(event)

        assert "room-123" in presence.rooms

        await presence.stop()

    async def test_room_added_calls_callback(self, mock_link):
        """room_added should call on_room_joined callback."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()

        joined_rooms = []

        async def on_joined(room_id, payload):
            joined_rooms.append((room_id, payload))

        presence.on_room_joined = on_joined

        event = make_room_added_event(room_id="room-123", title="Test")
        await presence._handle_room_added(event)

        # Payload is converted to dict via model_dump()
        assert len(joined_rooms) == 1
        assert joined_rooms[0][0] == "room-123"
        assert joined_rooms[0][1]["title"] == "Test"

        await presence.stop()

    async def test_room_added_respects_filter(self, mock_link):
        """room_added should respect room_filter."""

        def only_task_rooms(room):
            return room.get("type") == "task"

        presence = RoomPresence(
            mock_link, room_filter=only_task_rooms, auto_subscribe_existing=False
        )
        await presence.start()

        # Non-task room should be filtered (type="direct" in payload)
        event = make_room_added_event(room_id="room-123", type="direct")
        await presence._handle_room_added(event)

        assert "room-123" not in presence.rooms
        mock_link.subscribe_room.assert_not_called()

        await presence.stop()


class TestRoomPresenceRoomRemoved:
    """Test room_removed event handling."""

    async def test_room_removed_unsubscribes(self, mock_link):
        """room_removed should unsubscribe from room."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_removed_event(room_id="room-123")
        await presence._handle_room_removed(event)

        mock_link.unsubscribe_room.assert_called_with("room-123")

        await presence.stop()

    async def test_room_removed_untracks_room(self, mock_link):
        """room_removed should remove from presence.rooms."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_removed_event(room_id="room-123")
        await presence._handle_room_removed(event)

        assert "room-123" not in presence.rooms

        await presence.stop()

    async def test_room_removed_calls_callback(self, mock_link):
        """room_removed should call on_room_left callback."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        left_rooms = []

        async def on_left(room_id):
            left_rooms.append(room_id)

        presence.on_room_left = on_left

        event = make_room_removed_event(room_id="room-123")
        await presence._handle_room_removed(event)

        assert left_rooms == ["room-123"]

        await presence.stop()

    async def test_room_deleted_unsubscribes_and_untracks_room(self, mock_link):
        """room_deleted should reuse room cleanup behavior."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_deleted_event(room_id="room-123")
        await presence._on_platform_event(event)

        mock_link.unsubscribe_room.assert_called_with("room-123")
        assert "room-123" not in presence.rooms

        await presence.stop()

    async def test_room_deleted_calls_callback(self, mock_link):
        """room_deleted should call on_room_left callback once."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        left_rooms = []

        async def on_left(room_id):
            left_rooms.append(room_id)

        presence.on_room_left = on_left

        event = make_room_deleted_event(room_id="room-123")
        await presence._on_platform_event(event)

        assert left_rooms == ["room-123"]

        await presence.stop()


class TestRoomPresenceReconnect:
    """Test reconnect reconciliation behavior."""

    async def test_reconnect_unsubscribes_gone_rooms(self, mock_link):
        """Reconnect should unsubscribe rooms missing from the API."""
        room = MagicMock()
        room.id = "room-2"
        room.model_dump.return_value = {"id": "room-2"}
        mock_link.rest.agent_api_chats.list_agent_chats.return_value = MagicMock(
            data=[room],
            metadata=MagicMock(total_pages=1),
        )

        presence = RoomPresence(mock_link, auto_subscribe_existing=True)
        await presence.start()
        mock_link.subscribe_room.reset_mock()
        presence.rooms = {"room-1", "room-2"}

        event = ReconnectedEvent()
        await presence._on_platform_event(event)

        mock_link.unsubscribe_room.assert_called_once_with("room-1")
        assert presence.rooms == {"room-2"}

        await presence.stop()

    async def test_reconnect_subscribes_only_new_rooms(self, mock_link):
        """Reconnect should only join rooms that are new from the API."""
        room1 = MagicMock()
        room1.id = "room-1"
        room1.model_dump.return_value = {"id": "room-1"}
        room2 = MagicMock()
        room2.id = "room-2"
        room2.model_dump.return_value = {"id": "room-2"}
        mock_link.rest.agent_api_chats.list_agent_chats.return_value = MagicMock(
            data=[room1, room2],
            metadata=MagicMock(total_pages=1),
        )

        presence = RoomPresence(mock_link, auto_subscribe_existing=True)
        await presence.start()
        mock_link.subscribe_room.reset_mock()
        mock_link.unsubscribe_room.reset_mock()
        presence.rooms = {"room-1"}

        event = ReconnectedEvent()
        await presence._on_platform_event(event)

        mock_link.subscribe_room.assert_called_once_with("room-2")
        mock_link.unsubscribe_room.assert_not_called()
        assert presence.rooms == {"room-1", "room-2"}

        await presence.stop()

    async def test_reconnect_notifies_left_for_gone_rooms(self, mock_link):
        """Reconnect should fire on_room_left for rooms removed while offline."""
        mock_link.rest.agent_api_chats.list_agent_chats.return_value = MagicMock(
            data=[],
            metadata=MagicMock(total_pages=1),
        )
        left_rooms = []

        async def on_left(room_id):
            left_rooms.append(room_id)

        presence = RoomPresence(mock_link, auto_subscribe_existing=True)
        presence.on_room_left = on_left
        await presence.start()
        presence.rooms = {"room-1"}
        mock_link.unsubscribe_room.reset_mock()

        event = ReconnectedEvent()
        await presence._on_platform_event(event)

        assert left_rooms == ["room-1"]
        mock_link.unsubscribe_room.assert_called_once_with("room-1")
        assert presence.rooms == set()

        await presence.stop()


class TestRoomPresenceRoomEvents:
    """Test room-specific event handling."""

    async def test_room_event_forwards_to_callback(self, mock_link):
        """Room events should be forwarded to on_room_event."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        received_events = []

        async def on_event(room_id, event):
            received_events.append((room_id, event))

        presence.on_room_event = on_event

        event = make_message_event(room_id="room-123", msg_id="msg-1", content="Hello")
        await presence._handle_room_event(event)

        assert len(received_events) == 1
        assert received_events[0][0] == "room-123"
        assert received_events[0][1].payload.content == "Hello"

        await presence.stop()

    async def test_room_event_ignores_untracked_room(self, mock_link):
        """Events for untracked rooms should be ignored."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        # Don't add room-123 to tracked rooms

        received_events = []

        async def on_event(room_id, event):
            received_events.append((room_id, event))

        presence.on_room_event = on_event

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        await presence._handle_room_event(event)

        assert received_events == []

        await presence.stop()


class TestRoomPresenceEventRouting:
    """Test _on_platform_event routing."""

    async def test_routes_room_added(self, mock_link):
        """Should route room_added events correctly."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()

        event = make_room_added_event(room_id="room-123")
        await presence._on_platform_event(event)

        assert "room-123" in presence.rooms

        await presence.stop()

    async def test_routes_room_removed(self, mock_link):
        """Should route room_removed events correctly."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_removed_event(room_id="room-123")
        await presence._on_platform_event(event)

        assert "room-123" not in presence.rooms

        await presence.stop()

    async def test_routes_message_to_room_event(self, mock_link):
        """Should route message events to on_room_event."""
        presence = RoomPresence(mock_link, auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        received = []

        async def on_event(room_id, event):
            received.append(event.type)

        presence.on_room_event = on_event

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        await presence._on_platform_event(event)

        assert received == ["message_created"]

        await presence.stop()
