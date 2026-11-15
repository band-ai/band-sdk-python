"""BandLinkSubscriptions behavior."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from band.platform.link import BandLink


class TestBandLinkSubscriptions:
    """Test subscription management."""

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_agent_rooms_joins_channel(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_agent_rooms() should join agent rooms channel."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_agent_rooms("agent-123")

        mock_ws_client.join_agent_rooms_channel.assert_called_once()
        # Verify callbacks were passed
        call_kwargs = mock_ws_client.join_agent_rooms_channel.call_args[1]
        assert "on_room_added" in call_kwargs
        assert "on_room_removed" in call_kwargs

    async def test_subscribe_agent_rooms_raises_when_not_connected(self):
        """subscribe_agent_rooms() should raise when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.subscribe_agent_rooms("agent-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_joins_channels(self, mock_ws_class, mock_ws_client):
        """subscribe_room() should join chat room and participants channels."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        mock_ws_client.join_chat_room_channel.assert_called_once()
        mock_ws_client.join_room_participants_channel.assert_called_once()

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_tracks_subscription(
        self, mock_ws_class, mock_ws_client
    ):
        """subscribe_room() should track the room as subscribed."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")

        assert link.is_room_subscribed("room-123") is True

    @patch("band.platform.link.WebSocketClient")
    async def test_subscribe_room_idempotent(self, mock_ws_class, mock_ws_client):
        """subscribe_room() twice should not re-subscribe."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.subscribe_room("room-123")  # Second call

        # Should only join once
        assert mock_ws_client.join_chat_room_channel.call_count == 1

    async def test_subscribe_room_raises_when_not_connected(self):
        """subscribe_room() should raise when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.subscribe_room("room-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_leaves_channels(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should leave both channels."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.unsubscribe_room("room-123")

        mock_ws_client.leave_chat_room_channel.assert_called_once_with("room-123")
        mock_ws_client.leave_room_participants_channel.assert_called_once_with(
            "room-123"
        )

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_removes_from_tracking(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should remove the room from tracking."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        await link.unsubscribe_room("room-123")

        assert link.is_room_subscribed("room-123") is False

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_handles_leave_errors(
        self, mock_ws_class, mock_ws_client
    ):
        """unsubscribe_room() should handle errors gracefully."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-123")
        mock_ws_client.leave_chat_room_channel.side_effect = Exception("Leave failed")

        # Should not raise, just log warning
        await link.unsubscribe_room("room-123")

        # Room should still be removed from tracking despite the leave failure
        assert link.is_room_subscribed("room-123") is False

    async def test_unsubscribe_room_noop_when_not_subscribed(self):
        """unsubscribe_room() should be no-op for unsubscribed room."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        # Should not raise
        await link.unsubscribe_room("room-123")

    @patch("band.platform.link.WebSocketClient")
    async def test_unsubscribe_room_noop_when_connected_but_never_subscribed(
        self, mock_ws_class, mock_ws_client
    ):
        """A connected link with no prior subscribe_room() call must hit the
        tracker's own ``ticket is None`` no-op — distinct from the
        not-connected case above, which short-circuits earlier on ``self._ws``
        and never reaches the tracker at all."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()

        await link.unsubscribe_room("room-123")

        mock_ws_client.leave_chat_room_channel.assert_not_called()
        mock_ws_client.leave_room_participants_channel.assert_not_called()
