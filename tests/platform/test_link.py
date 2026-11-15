"""BandLinkConstruction behavior."""

from __future__ import annotations

from band.platform.link import BandLink


class TestBandLinkConstruction:
    """Test BandLink initialization."""

    def test_init_stores_credentials(self):
        """Should store agent_id, api_key, and URLs."""
        link = BandLink(
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
        link = BandLink(
            agent_id="agent-123",
            api_key="test-key",
        )

        assert link.rest is not None

    def test_init_starts_disconnected(self):
        """Should start in disconnected state."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        assert link.is_connected is False
        assert link._ws is None
        assert link.is_room_subscribed("room-123") is False

    def test_init_empty_event_queue(self):
        """Should start with empty event queue."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        assert link._event_queue.empty()
