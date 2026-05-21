"""Tests for RoomPresence contact event routing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from thenvoi.platform.event import (
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
    MessageEvent,
    RoomAddedEvent,
)
from thenvoi.client.streaming import (
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
    MessageCreatedPayload,
    RoomAddedPayload,
)
from thenvoi.runtime.presence import RoomPresence


@pytest.fixture
def mock_link():
    """Mock BandLink for testing."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = True
    link.rest = MagicMock()
    link.rest.agent_api_chats = MagicMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    return link


@pytest.fixture
def sample_contact_request_event():
    """Sample ContactRequestReceivedEvent."""
    return ContactRequestReceivedEvent(
        payload=ContactRequestReceivedPayload(
            id="req-123",
            from_handle="@alice",
            from_name="Alice",
            message="Hello!",
            status="pending",
            inserted_at="2024-01-01T00:00:00Z",
        )
    )


@pytest.fixture
def sample_contact_added_event():
    """Sample ContactAddedEvent."""
    return ContactAddedEvent(
        payload=ContactAddedPayload(
            id="contact-123",
            handle="@bob",
            name="Bob",
            type="User",
            inserted_at="2024-01-01T00:00:00Z",
        )
    )


@pytest.fixture
def sample_message_event():
    """Sample MessageEvent for a room."""
    return MessageEvent(
        room_id="room-123",
        payload=MessageCreatedPayload(
            id="msg-123",
            content="Hello",
            message_type="text",
            sender_type="User",
            sender_id="user-123",
            sender_name="Alice",
            chat_room_id="room-123",
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
        raw={},
    )


@pytest.fixture
def sample_room_added_event():
    """Sample RoomAddedEvent."""
    return RoomAddedEvent(
        room_id="room-456",
        payload=RoomAddedPayload(
            id="room-456",
            title="Test Room",
            task_id=None,
            inserted_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        ),
        raw={},
    )


class TestContactEventRouting:
    """Tests for contact event routing to on_contact_event callback."""

    async def test_contact_event_routed_to_callback(
        self, mock_link, sample_contact_request_event
    ):
        """ContactEvents should be routed to on_contact_event callback."""
        presence = RoomPresence(link=mock_link, auto_subscribe_existing=False)
        contact_callback = AsyncMock()
        presence.on_contact_event = contact_callback

        # Process contact event
        await presence._on_platform_event(sample_contact_request_event)

        # Verify callback was called with the event
        contact_callback.assert_called_once_with(sample_contact_request_event)

    async def test_contact_event_not_routed_to_room(
        self, mock_link, sample_contact_added_event
    ):
        """ContactEvents should NOT be routed to room event handlers."""
        presence = RoomPresence(link=mock_link, auto_subscribe_existing=False)
        room_callback = AsyncMock()
        presence.on_room_event = room_callback
        presence.on_contact_event = AsyncMock()  # Set up contact handler

        # Add a room to track
        presence.rooms.add("room-123")

        # Process contact event
        await presence._on_platform_event(sample_contact_added_event)

        # Room callback should NOT be called
        room_callback.assert_not_called()

    async def test_room_events_still_work(
        self, mock_link, sample_message_event, sample_room_added_event
    ):
        """MessageEvent and RoomAddedEvent should still work normally."""
        presence = RoomPresence(link=mock_link, auto_subscribe_existing=False)
        room_callback = AsyncMock()
        room_joined_callback = AsyncMock()
        contact_callback = AsyncMock()

        presence.on_room_event = room_callback
        presence.on_room_joined = room_joined_callback
        presence.on_contact_event = contact_callback

        # Add room to track for message events
        presence.rooms.add("room-123")

        # Process room added event
        await presence._on_platform_event(sample_room_added_event)
        room_joined_callback.assert_called_once()

        # Process message event
        await presence._on_platform_event(sample_message_event)
        room_callback.assert_called_once_with("room-123", sample_message_event)

        # Contact callback should not be called for room events
        contact_callback.assert_not_called()

    async def test_no_callback_ignores_contact_events(
        self, mock_link, sample_contact_request_event
    ):
        """Without on_contact_event callback, contact events are ignored silently."""
        presence = RoomPresence(link=mock_link, auto_subscribe_existing=False)
        # No on_contact_event set

        # Should not raise
        await presence._on_platform_event(sample_contact_request_event)

    async def test_all_contact_event_types_routed(self, mock_link):
        """All contact event types should be routed to on_contact_event."""
        presence = RoomPresence(link=mock_link, auto_subscribe_existing=False)
        contact_callback = AsyncMock()
        presence.on_contact_event = contact_callback

        events = [
            ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id="req-1",
                    from_handle="@alice",
                    from_name="Alice",
                    message=None,
                    status="pending",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            ),
            ContactRequestUpdatedEvent(
                payload=ContactRequestUpdatedPayload(
                    id="req-1",
                    status="approved",
                )
            ),
            ContactAddedEvent(
                payload=ContactAddedPayload(
                    id="contact-1",
                    handle="@alice",
                    name="Alice",
                    type="User",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            ),
            ContactRemovedEvent(payload=ContactRemovedPayload(id="contact-1")),
        ]

        for event in events:
            await presence._on_platform_event(event)

        # All events should have been routed
        assert contact_callback.call_count == 4

    async def test_contact_event_callback_error_logged(
        self, mock_link, sample_contact_request_event
    ):
        """Errors in contact event callback should be logged, not raised."""
        presence = RoomPresence(link=mock_link, auto_subscribe_existing=False)

        async def failing_callback(event):
            raise ValueError("Callback failed!")

        presence.on_contact_event = failing_callback

        # Should not raise
        await presence._on_platform_event(sample_contact_request_event)
