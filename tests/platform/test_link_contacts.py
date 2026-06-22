"""Unit tests for BandLink contact subscription."""

import pytest
from unittest.mock import AsyncMock, patch

from band.platform.link import BandLink
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


@pytest.fixture
def mock_ws_client():
    """Mock WebSocketClient for testing."""
    ws = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)
    ws.join_agent_contacts_channel = AsyncMock()
    ws.leave_agent_contacts_channel = AsyncMock()
    ws.run_forever = AsyncMock()
    return ws


class TestContactSubscription:
    """Tests for contact channel subscription."""

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_agent_contacts_joins_channel(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_agent_contacts() should join agent contacts channel."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_contacts("agent-123")

        mock_ws_client.join_agent_contacts_channel.assert_called_once()
        call_args = mock_ws_client.join_agent_contacts_channel.call_args
        assert call_args[0][0] == "agent-123"  # agent_id

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_agent_contacts_passes_all_handlers(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_agent_contacts() should pass all 4 event handlers."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_contacts("agent-123")

        call_kwargs = mock_ws_client.join_agent_contacts_channel.call_args[1]
        assert "on_contact_request_received" in call_kwargs
        assert "on_contact_request_updated" in call_kwargs
        assert "on_contact_added" in call_kwargs
        assert "on_contact_removed" in call_kwargs

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_agent_contacts_requires_connection(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_agent_contacts() should raise when not connected."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        # Not connected

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.subscribe_agent_contacts("agent-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_agent_contacts_leaves_channel(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_agent_contacts() should leave agent contacts channel."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.unsubscribe_agent_contacts()

        mock_ws_client.leave_agent_contacts_channel.assert_called_once_with("agent-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_agent_contacts_handles_errors(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_agent_contacts() should handle errors gracefully."""
        mock_ws_class.return_value = mock_ws_client
        mock_ws_client.leave_agent_contacts_channel.side_effect = Exception(
            "Leave failed"
        )

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        # Should not raise
        await link.unsubscribe_agent_contacts()

    async def test_unsubscribe_agent_contacts_noop_when_not_connected(self):
        """unsubscribe_agent_contacts() should be no-op when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        # Should not raise
        await link.unsubscribe_agent_contacts()


class TestContactEventHandlers:
    """Tests for contact event handlers."""

    @patch("band.platform.link.WebSocketClient")
    async def test_on_contact_request_received_queues_event(
        self, mock_ws_class, mock_ws_client
    ):
        """_on_contact_request_received() should queue ContactRequestReceivedEvent."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        payload = ContactRequestReceivedPayload(
            id="req-123",
            from_handle="john_doe",
            from_name="John Doe",
            status="pending",
            inserted_at="2026-02-09T10:30:00Z",
        )
        await link._on_contact_request_received(payload)

        event = await link._event_queue.get()
        assert isinstance(event, ContactRequestReceivedEvent)
        assert event.payload.id == "req-123"
        assert event.room_id is None

    @patch("band.platform.link.WebSocketClient")
    async def test_on_contact_request_updated_queues_event(
        self, mock_ws_class, mock_ws_client
    ):
        """_on_contact_request_updated() should queue ContactRequestUpdatedEvent."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        payload = ContactRequestUpdatedPayload(
            id="req-123",
            status="approved",
        )
        await link._on_contact_request_updated(payload)

        event = await link._event_queue.get()
        assert isinstance(event, ContactRequestUpdatedEvent)
        assert event.payload.status == "approved"
        assert event.room_id is None

    @patch("band.platform.link.WebSocketClient")
    async def test_on_contact_added_queues_event(self, mock_ws_class, mock_ws_client):
        """_on_contact_added() should queue ContactAddedEvent."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        payload = ContactAddedPayload(
            id="contact-123",
            handle="jane_smith",
            name="Jane Smith",
            type="User",
            inserted_at="2026-02-09T10:35:00Z",
        )
        await link._on_contact_added(payload)

        event = await link._event_queue.get()
        assert isinstance(event, ContactAddedEvent)
        assert event.payload.name == "Jane Smith"
        assert event.room_id is None

    @patch("band.platform.link.WebSocketClient")
    async def test_on_contact_removed_queues_event(self, mock_ws_class, mock_ws_client):
        """_on_contact_removed() should queue ContactRemovedEvent."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        payload = ContactRemovedPayload(id="contact-123")
        await link._on_contact_removed(payload)

        event = await link._event_queue.get()
        assert isinstance(event, ContactRemovedEvent)
        assert event.payload.id == "contact-123"
        assert event.room_id is None


class TestPublicQueueMethod:
    """Tests for public queue_event() method."""

    @patch("band.platform.link.WebSocketClient")
    async def test_queue_event_public_method(self, mock_ws_class, mock_ws_client):
        """queue_event() should add event to queue (public API)."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        event = ContactAddedEvent(
            payload=ContactAddedPayload(
                id="contact-123",
                handle="test",
                name="Test",
                type="User",
                inserted_at="2026-01-01T00:00:00Z",
            )
        )
        link.queue_event(event)

        queued = await link._event_queue.get()
        assert queued is event

    def test_queue_event_works_without_connection(self):
        """queue_event() should work even when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        event = ContactRemovedEvent(payload=ContactRemovedPayload(id="contact-123"))
        link.queue_event(event)

        assert link._event_queue.qsize() == 1
