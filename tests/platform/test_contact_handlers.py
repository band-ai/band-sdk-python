"""ContactEventHandlers behavior."""

from __future__ import annotations

from unittest.mock import patch

from band.client.streaming import (
    ContactAddedPayload,
    ContactRemovedPayload,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    WireEvent,
)
from band.platform.event import (
    ContactAddedEvent,
    ContactRemovedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
)
from band.platform.link import BandLink


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
    async def test_on_contact_request_received_with_absent_sender_still_queues(
        self, mock_ws_class, mock_ws_client
    ):
        """A wire payload with from_handle/from_name absent -- which
        band-sdk-core accepts (contact_request_received's `compact/1` drops
        the keys, it does not send `null`) -- must still reach the queue.

        `_on_contact_request_received` unconditionally logs `payload.from_name`
        and `payload.from_handle`; before these fields were made Optional, a
        `from_wire`-hydrated payload missing them left the attributes unset via
        `model_construct`, so that log line raised `AttributeError` and this
        real event was silently dropped instead of queued.
        """
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        payload = ContactRequestReceivedPayload.from_wire(
            WireEvent.CONTACT_REQUEST_RECEIVED,
            {
                "id": "req-456",
                "status": "pending",
                "inserted_at": "2026-02-09T10:30:00Z",
            },
        )
        await link._on_contact_request_received(payload)

        event = await link._event_queue.get()
        assert isinstance(event, ContactRequestReceivedEvent)
        assert event.payload.id == "req-456"
        assert event.payload.from_handle is None
        assert event.payload.from_name is None

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
