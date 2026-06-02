"""Tests for broadcast changes functionality."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.event import (
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
    ContactAddedEvent,
    ContactRemovedEvent,
)
from band.client.streaming import (
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)
from band.runtime.contact_handler import ContactEventHandler
from band.runtime.types import ContactEventConfig, ContactEventStrategy


@pytest.fixture
def mock_link():
    """Mock BandLink for testing."""
    link = MagicMock()
    link.rest = MagicMock()
    # Mock for HUB_ROOM tests
    mock_chat_response = MagicMock()
    mock_chat_response.data = MagicMock()
    mock_chat_response.data.id = "hub-room-123"
    link.rest.agent_api_chats = MagicMock()
    link.rest.agent_api_chats.create_agent_chat = AsyncMock(
        return_value=mock_chat_response
    )
    link.rest.agent_api_events = MagicMock()
    link.rest.agent_api_events.create_agent_chat_event = AsyncMock()
    return link


@pytest.fixture
def sample_contact_added_event():
    """Sample ContactAddedEvent."""
    return ContactAddedEvent(
        payload=ContactAddedPayload(
            id="contact-123",
            handle="@bob",
            name="Bob Smith",
            type="User",
            inserted_at="2024-01-01T00:00:00Z",
        )
    )


@pytest.fixture
def sample_contact_removed_event():
    """Sample ContactRemovedEvent."""
    return ContactRemovedEvent(payload=ContactRemovedPayload(id="contact-456"))


@pytest.fixture
def sample_request_received_event():
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


class TestBroadcastQueuing:
    """Tests for broadcast message queuing."""

    async def test_broadcast_queues_contact_added(
        self, mock_link, sample_contact_added_event
    ):
        """ContactAddedEvent should queue broadcast message."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_added_event)

        assert len(broadcast_messages) == 1
        assert "@bob" in broadcast_messages[0]
        assert "Bob Smith" in broadcast_messages[0]
        assert "is now a contact" in broadcast_messages[0]

    async def test_broadcast_queues_contact_removed(
        self, mock_link, sample_contact_removed_event
    ):
        """ContactRemovedEvent should queue broadcast message."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_removed_event)

        assert len(broadcast_messages) == 1
        assert "contact-456" in broadcast_messages[0]
        assert "removed" in broadcast_messages[0]

    async def test_broadcast_ignores_request_events(
        self, mock_link, sample_request_received_event
    ):
        """Request events should not trigger broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_request_received_event)

        # No broadcast for request events
        assert len(broadcast_messages) == 0

    async def test_broadcast_ignores_request_updated_events(self, mock_link):
        """Request updated events should not trigger broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        event = ContactRequestUpdatedEvent(
            payload=ContactRequestUpdatedPayload(
                id="req-123",
                status="approved",
            )
        )

        await handler.handle(event)

        # No broadcast for request events
        assert len(broadcast_messages) == 0


class TestBroadcastFormat:
    """Tests for broadcast message formatting."""

    async def test_broadcast_format_contact_added(
        self, mock_link, sample_contact_added_event
    ):
        """ContactAddedEvent should be formatted as '@handle (name) is now a contact'."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_added_event)

        expected = "@bob (Bob Smith) is now a contact"
        assert broadcast_messages[0] == expected

    async def test_broadcast_format_contact_removed(
        self, mock_link, sample_contact_removed_event
    ):
        """ContactRemovedEvent should be formatted as 'Contact X was removed'."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_removed_event)

        expected = "Contact contact-456 was removed"
        assert broadcast_messages[0] == expected


class TestBroadcastDisabled:
    """Tests for broadcast disabled state."""

    async def test_broadcast_disabled_by_default(
        self, mock_link, sample_contact_added_event
    ):
        """broadcast_changes=False should not trigger broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=False,  # Default
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_added_event)

        assert len(broadcast_messages) == 0

    async def test_broadcast_no_callback_provided(
        self, mock_link, sample_contact_added_event
    ):
        """No on_broadcast callback should not cause errors."""
        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        # No on_broadcast callback
        handler = ContactEventHandler(config, mock_link, on_broadcast=None)

        # Should not raise
        await handler.handle(sample_contact_added_event)


class TestBroadcastComposable:
    """Tests for broadcast composability with strategies."""

    async def test_broadcast_composable_with_callback(self, mock_link):
        """CALLBACK + broadcast_changes=True should both work."""
        broadcast_messages = []
        callback_events = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        async def capture_callback(event, tools):
            callback_events.append(event)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=capture_callback,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        event = ContactAddedEvent(
            payload=ContactAddedPayload(
                id="contact-789",
                handle="@charlie",
                name="Charlie",
                type="User",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )
        await handler.handle(event)

        # Both callback and broadcast should fire
        assert len(callback_events) == 1
        assert len(broadcast_messages) == 1

    async def test_broadcast_composable_with_hub_room(self, mock_link):
        """HUB_ROOM + broadcast_changes=True should both work."""
        broadcast_messages = []
        hub_events = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        async def capture_hub_event(room_id, event):
            hub_events.append((room_id, event))

        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(
            config,
            mock_link,
            on_broadcast=capture_broadcast,
            on_hub_event=capture_hub_event,
        )

        # Initialize and mark ready
        await handler.initialize_hub_room()
        handler.mark_hub_room_ready()

        event = ContactAddedEvent(
            payload=ContactAddedPayload(
                id="contact-999",
                handle="@dave",
                name="Dave",
                type="Agent",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )
        await handler.handle(event)

        # Both HUB_ROOM (injects event) and broadcast should work
        assert len(broadcast_messages) == 1
        assert len(hub_events) == 1

    async def test_broadcast_composable_with_disabled(
        self, mock_link, sample_contact_added_event
    ):
        """DISABLED + broadcast_changes=True should only broadcast."""
        broadcast_messages = []

        def capture_broadcast(msg):
            broadcast_messages.append(msg)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.DISABLED,
            broadcast_changes=True,
        )
        handler = ContactEventHandler(config, mock_link, on_broadcast=capture_broadcast)

        await handler.handle(sample_contact_added_event)

        # Broadcast fires even with DISABLED strategy
        assert len(broadcast_messages) == 1
        # No HUB_ROOM event sent
        mock_link.rest.agent_api_events.create_agent_chat_event.assert_not_called()
