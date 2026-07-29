"""Tests for RoomPresence."""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from band.runtime.presence import RoomPresence

# Import test helpers from conftest
from band.platform.event import ReconnectedEvent, WebSocketDisconnectedEvent

from tests.conftest import (
    make_message_event,
    make_room_added_event,
    make_room_deleted_event,
    make_room_removed_event,
)


@pytest.fixture
def mock_link():
    """Mock BandLink for testing RoomPresence."""
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


@pytest.fixture
async def presences(mock_link):
    """Build presences that are stopped however the test ends.

    A started presence owns an event-consumer task, so a test that leaves one
    running leaks it into the rest of the session.
    """
    built = []

    def build(**kwargs):
        built.append(RoomPresence(mock_link, **kwargs))
        return built[-1]

    yield build
    for presence in built:
        await presence.stop()


class TestRoomPresenceStart:
    """Test RoomPresence.start()."""

    async def test_start_creates_event_task(self, mock_link, presences):
        """start() should create internal event consumer task."""
        presence = presences(auto_subscribe_existing=False)

        await presence.start()

        assert presence._event_task is not None

    async def test_start_connects_if_not_connected(self, mock_link, presences):
        """start() should connect link if not already connected."""
        mock_link.is_connected = False
        presence = presences(auto_subscribe_existing=False)

        await presence.start()

        mock_link.connect.assert_called_once()

    async def test_start_skips_connect_if_connected(self, mock_link, presences):
        """start() should not reconnect if already connected."""
        mock_link.is_connected = True
        presence = presences(auto_subscribe_existing=False)

        await presence.start()

        mock_link.connect.assert_not_called()

    async def test_start_subscribes_to_agent_rooms(self, mock_link, presences):
        """start() should subscribe to agent rooms channel."""
        presence = presences(auto_subscribe_existing=False)

        await presence.start()

        mock_link.subscribe_agent_rooms.assert_called_once_with("agent-123")

    async def test_start_subscribes_existing_rooms(self, mock_link, presences):
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

        presence = presences(auto_subscribe_existing=True)
        await presence.start()

        assert "room-1" in presence.rooms
        assert "room-2" in presence.rooms
        assert mock_link.subscribe_room.call_count == 2


class TestRoomPresenceStop:
    """Test RoomPresence.stop()."""

    async def test_stop_clears_rooms(self, mock_link, presences):
        """stop() should clear tracked rooms."""
        presence = presences(auto_subscribe_existing=False)
        presence.rooms.add("room-1")
        presence.rooms.add("room-2")

        await presence.stop()

        assert presence.rooms == set()

    async def test_stop_calls_on_room_left(self, mock_link, presences):
        """stop() should call on_room_left for each room."""
        presence = presences(auto_subscribe_existing=False)
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

    async def test_room_added_subscribes_tracks_and_announces(
        self, mock_link, presences
    ):
        """One transition, one outcome: the room is live and the caller knows."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        joined = []

        async def on_joined(room_id, payload):
            joined.append((room_id, payload))

        presence.on_room_joined = on_joined

        await presence._handle_room_added(
            make_room_added_event(room_id="room-123", title="Test")
        )

        mock_link.subscribe_room.assert_called_with("room-123")
        assert presence.rooms == {"room-123"}
        assert joined == [("room-123", ANY)]
        assert joined[0][1]["title"] == "Test", "the payload reaches the callback"

    async def test_room_added_respects_filter(self, mock_link, presences):
        """room_added should respect room_filter."""

        def only_task_rooms(room):
            return room.get("type") == "task"

        presence = presences(room_filter=only_task_rooms, auto_subscribe_existing=False)
        await presence.start()

        # Non-task room should be filtered (type="direct" in payload)
        event = make_room_added_event(room_id="room-123", type="direct")
        await presence._handle_room_added(event)

        assert "room-123" not in presence.rooms
        mock_link.subscribe_room.assert_not_called()


def chat_row(room_id):
    """One room as the chats listing returns it."""
    room = MagicMock()
    room.id = room_id
    room.model_dump.return_value = {"id": room_id}
    return room


def listing(*pages):
    """A chats listing answering each page in turn."""
    return AsyncMock(
        side_effect=[
            MagicMock(data=list(page), metadata=MagicMock(total_pages=len(pages)))
            for page in pages
        ]
    )


class TestJoiningOnce:
    """A room is joined once, however many sources name it."""

    async def test_a_room_in_both_the_snapshot_and_an_event_joins_once(
        self, mock_link, presences
    ):
        """The agent's room channel is live before the snapshot is read, so a
        room added right then arrives by both routes."""
        mock_link.rest.agent_api_chats.list_agent_chats = listing([chat_row("room-1")])
        joined = []
        presence = presences(auto_subscribe_existing=True)

        async def note(room_id, payload):
            joined.append(room_id)

        presence.on_room_joined = note
        await presence.start()

        await presence._on_platform_event(make_room_added_event("room-1"))

        assert joined == ["room-1"], "the callback ran again for a room already joined"
        assert mock_link.subscribe_room.call_count == 1

    async def test_a_room_on_two_pages_of_one_snapshot_joins_once(
        self, mock_link, presences
    ):
        """Offset paging repeats a room when the listing shifts under it."""
        mock_link.rest.agent_api_chats.list_agent_chats = listing(
            [chat_row("room-1")], [chat_row("room-1")]
        )
        joined = []
        presence = presences(auto_subscribe_existing=True)

        async def note(room_id, payload):
            joined.append(room_id)

        presence.on_room_joined = note

        await presence.start()

        assert joined == ["room-1"]
        assert mock_link.subscribe_room.call_count == 1

    async def test_a_failing_callback_does_not_untrack_the_joined_room(
        self, mock_link, presences
    ):
        """The subscription succeeded; dropping the room would silently discard
        every event it goes on to deliver."""
        mock_link.rest.agent_api_chats.list_agent_chats = listing([chat_row("room-1")])
        presence = presences(auto_subscribe_existing=True)
        presence.on_room_joined = AsyncMock(side_effect=RuntimeError("callback boom"))

        await presence.start()

        assert presence.rooms == {"room-1"}


class TestRoomPresenceRoomRemoved:
    """Test room_removed event handling."""

    async def test_room_removed_unsubscribes(self, mock_link, presences):
        """room_removed should unsubscribe from room."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_removed_event(room_id="room-123")
        await presence._handle_room_removed(event)

        mock_link.unsubscribe_room.assert_called_with("room-123")

    async def test_room_removed_untracks_room(self, mock_link, presences):
        """room_removed should remove from presence.rooms."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_removed_event(room_id="room-123")
        await presence._handle_room_removed(event)

        assert "room-123" not in presence.rooms

    async def test_room_removed_calls_callback(self, mock_link, presences):
        """room_removed should call on_room_left callback."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        left_rooms = []

        async def on_left(room_id):
            left_rooms.append(room_id)

        presence.on_room_left = on_left

        event = make_room_removed_event(room_id="room-123")
        await presence._handle_room_removed(event)

        assert left_rooms == ["room-123"]

    async def test_room_deleted_unsubscribes_and_untracks_room(
        self, mock_link, presences
    ):
        """room_deleted should reuse room cleanup behavior."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_deleted_event(room_id="room-123")
        await presence._on_platform_event(event)

        mock_link.unsubscribe_room.assert_called_with("room-123")
        assert "room-123" not in presence.rooms

    async def test_room_deleted_calls_callback(self, mock_link, presences):
        """room_deleted should call on_room_left callback once."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        left_rooms = []

        async def on_left(room_id):
            left_rooms.append(room_id)

        presence.on_room_left = on_left

        event = make_room_deleted_event(room_id="room-123")
        await presence._on_platform_event(event)

        assert left_rooms == ["room-123"]


class TestRoomPresenceReconnect:
    """Test reconnect reconciliation behavior."""

    async def test_reconnect_unsubscribes_gone_rooms(self, mock_link, presences):
        """Reconnect should unsubscribe rooms missing from the API."""
        room = MagicMock()
        room.id = "room-2"
        room.model_dump.return_value = {"id": "room-2"}
        mock_link.rest.agent_api_chats.list_agent_chats.return_value = MagicMock(
            data=[room],
            metadata=MagicMock(total_pages=1),
        )

        presence = presences(auto_subscribe_existing=True)
        await presence.start()
        mock_link.subscribe_room.reset_mock()
        presence.rooms = {"room-1", "room-2"}

        event = ReconnectedEvent()
        await presence._on_platform_event(event)

        mock_link.unsubscribe_room.assert_called_once_with("room-1")
        assert presence.rooms == {"room-2"}

    async def test_reconnect_subscribes_only_new_rooms(self, mock_link, presences):
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

        presence = presences(auto_subscribe_existing=True)
        await presence.start()
        mock_link.subscribe_room.reset_mock()
        mock_link.unsubscribe_room.reset_mock()
        presence.rooms = {"room-1"}

        event = ReconnectedEvent()
        await presence._on_platform_event(event)

        mock_link.subscribe_room.assert_called_once_with("room-2")
        mock_link.unsubscribe_room.assert_not_called()
        assert presence.rooms == {"room-1", "room-2"}

    async def test_reconnect_notifies_left_for_gone_rooms(self, mock_link, presences):
        """Reconnect should fire on_room_left for rooms removed while offline."""
        mock_link.rest.agent_api_chats.list_agent_chats.return_value = MagicMock(
            data=[],
            metadata=MagicMock(total_pages=1),
        )
        left_rooms = []

        async def on_left(room_id):
            left_rooms.append(room_id)

        presence = presences(auto_subscribe_existing=True)
        presence.on_room_left = on_left
        await presence.start()
        presence.rooms = {"room-1"}
        mock_link.unsubscribe_room.reset_mock()

        event = ReconnectedEvent()
        await presence._on_platform_event(event)

        assert left_rooms == ["room-1"]
        mock_link.unsubscribe_room.assert_called_once_with("room-1")
        assert presence.rooms == set()

    async def test_reconnect_forwards_event_to_surviving_rooms(
        self, mock_link, presences
    ):
        """Reconnect should notify surviving rooms even when no rooms are newly joined."""
        room = MagicMock()
        room.id = "room-1"
        room.model_dump.return_value = {"id": "room-1"}
        mock_link.rest.agent_api_chats.list_agent_chats.return_value = MagicMock(
            data=[room],
            metadata=MagicMock(total_pages=1),
        )
        received = []

        async def on_event(room_id, event):
            received.append((room_id, event))

        presence = presences(auto_subscribe_existing=True)
        presence.on_room_event = on_event
        await presence.start()
        mock_link.subscribe_room.reset_mock()
        mock_link.unsubscribe_room.reset_mock()
        presence.rooms = {"room-1"}

        await presence._on_platform_event(ReconnectedEvent())

        assert mock_link.subscribe_room.call_count == 0
        assert mock_link.unsubscribe_room.call_count == 0
        assert len(received) == 1
        assert received[0][0] == "room-1"
        assert isinstance(received[0][1], ReconnectedEvent)


class TestRoomPresenceRoomEvents:
    """Test room-specific event handling."""

    async def test_room_event_forwards_to_callback(self, mock_link, presences):
        """Room events should be forwarded to on_room_event."""
        presence = presences(auto_subscribe_existing=False)
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

    async def test_room_event_ignores_untracked_room(self, mock_link, presences):
        """Events for untracked rooms should be ignored."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        # Don't add room-123 to tracked rooms

        received_events = []

        async def on_event(room_id, event):
            received_events.append((room_id, event))

        presence.on_room_event = on_event

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        await presence._handle_room_event(event)

        assert received_events == []


class TestRoomPresenceEventRouting:
    """Test _on_platform_event routing."""

    async def test_routes_room_added(self, mock_link, presences):
        """Should route room_added events correctly."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()

        event = make_room_added_event(room_id="room-123")
        await presence._on_platform_event(event)

        assert "room-123" in presence.rooms

    async def test_routes_room_removed(self, mock_link, presences):
        """Should route room_removed events correctly."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        event = make_room_removed_event(room_id="room-123")
        await presence._on_platform_event(event)

        assert "room-123" not in presence.rooms

    async def test_routes_message_to_room_event(self, mock_link, presences):
        """Should route message events to on_room_event."""
        presence = presences(auto_subscribe_existing=False)
        await presence.start()
        presence.rooms.add("room-123")

        received = []

        async def on_event(room_id, event):
            received.append(event.type)

        presence.on_room_event = on_event

        event = make_message_event(room_id="room-123", msg_id="msg-1")
        await presence._on_platform_event(event)

        assert received == ["message_created"]


class TestDisconnectedDispatch:
    """A terminal disconnect must reach its owner, not fall through the match."""

    @pytest.mark.asyncio
    async def test_terminal_disconnect_fires_the_hook(self, mock_link, presences):
        presence = presences(auto_subscribe_existing=False)
        presence.on_disconnected = AsyncMock()

        await presence._on_platform_event(WebSocketDisconnectedEvent())

        presence.on_disconnected.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failing_hook_does_not_kill_the_event_consumer(
        self, mock_link, presences
    ):
        presence = presences(auto_subscribe_existing=False)
        presence.on_disconnected = AsyncMock(side_effect=RuntimeError("boom"))

        await presence._on_platform_event(WebSocketDisconnectedEvent())
