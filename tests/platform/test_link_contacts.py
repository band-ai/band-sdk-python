"""ContactSubscription behavior."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from band.platform.link import BandLink


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
        """unsubscribe_agent_contacts() should leave an actually-joined
        agent contacts channel."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_contacts("agent-123")
        await link.unsubscribe_agent_contacts()

        mock_ws_client.leave_agent_contacts_channel.assert_called_once_with("agent-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_agent_contacts_noop_when_never_subscribed(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_agent_contacts() is a true no-op when the topic was
        never joined — the tracker's leave_agent_topic() returns None rather
        than issuing a leave the transport would just reject."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.unsubscribe_agent_contacts()

        mock_ws_client.leave_agent_contacts_channel.assert_not_called()

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_agent_contacts_handles_errors(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_agent_contacts() should handle errors gracefully."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_contacts("agent-123")
        mock_ws_client.leave_agent_contacts_channel.side_effect = Exception(
            "Leave failed"
        )

        # Should not raise
        await link.unsubscribe_agent_contacts()

        mock_ws_client.leave_agent_contacts_channel.assert_called_once_with("agent-123")

    async def test_unsubscribe_agent_contacts_noop_when_not_connected(self):
        """unsubscribe_agent_contacts() should be no-op when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        # Should not raise
        await link.unsubscribe_agent_contacts()
