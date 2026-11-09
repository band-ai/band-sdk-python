"""PublicQueueMethod behavior."""

from __future__ import annotations

from unittest.mock import patch

from band.client.streaming import (
    ContactAddedPayload,
    ContactRemovedPayload,
)
from band.platform.event import (
    ContactAddedEvent,
    ContactRemovedEvent,
)
from band.platform.link import BandLink


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
