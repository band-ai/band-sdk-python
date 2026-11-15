"""BandLinkConnection behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from band.platform.link import BandLink
from tests.platform.conftest import (
    cancelled_mid_await,
)
from tests.signalcases import INSTALL_SIGNAL_HANDLER_CASES, INSTALL_SIGNAL_HANDLER_IDS


class TestBandLinkConnection:
    """Test connection lifecycle."""

    @patch("band.platform.link.WebSocketClient")
    async def test_connect_when_already_connected_logs_warning(
        self, mock_ws_class, mock_ws_client
    ):
        """connect() when already connected should log warning and return."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.connect()  # Second call

        # Should only create WS once
        assert mock_ws_class.call_count == 1

    @patch("band.platform.link.WebSocketClient")
    async def test_concurrent_connect_only_creates_one_websocket(
        self, mock_ws_class, mock_ws_client
    ):
        """Two genuinely concurrent connect() calls must not both build a
        WebSocketClient: the guard has to be the synchronous `self._connecting`
        assignment, not `self._is_connected` (only set true after two awaits)
        or `self._ws` (only assigned once fully connected) — otherwise the
        second call races past the flag and leaks the first client."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")

        await asyncio.gather(link.connect(), link.connect())

        assert mock_ws_class.call_count == 1
        assert link.is_connected is True

    @patch("band.platform.link.WebSocketClient")
    async def test_cancelled_connect_closes_the_half_opened_client_and_allows_retry(
        self, mock_ws_class, mock_ws_client
    ):
        """A connect() cancelled while awaiting __aenter__() must close the
        half-opened client it made, and a later connect() must actually
        retry -- not see a leftover non-None _ws and silently no-op forever
        with is_connected stuck False."""
        mock_ws_class.return_value = mock_ws_client
        link = BandLink(agent_id="agent-123", api_key="test-key")

        async with cancelled_mid_await(mock_ws_client.__aenter__, link.connect()):
            pass

        assert link._ws is None
        assert link.is_connected is False
        assert link._connecting is False
        mock_ws_client.__aexit__.assert_called_once()

        await link.connect()

        assert link.is_connected is True

    @patch("band.platform.link.WebSocketClient")
    async def test_disconnect_exits_websocket_context(
        self, mock_ws_class, mock_ws_client
    ):
        """disconnect() should exit WebSocket context."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.disconnect()

        mock_ws_client.__aexit__.assert_called_once_with(None, None, None)
        assert link.is_connected is False
        assert link._ws is None

    @patch("band.platform.link.WebSocketClient")
    async def test_disconnect_clears_subscribed_rooms(
        self, mock_ws_class, mock_ws_client
    ):
        """disconnect() should clear tracked subscriptions."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-1")
        await link.subscribe_room("room-2")

        await link.disconnect()

        assert link.is_room_subscribed("room-1") is False
        assert link.is_room_subscribed("room-2") is False

    @patch("band.platform.link.WebSocketClient")
    async def test_reconnect_keeps_tracked_room_subscriptions(
        self, mock_ws_class, mock_ws_client
    ):
        """_on_reconnected() should preserve room tracking for PHX
        re-subscriptions — a normally-subscribed room needs no reconciliation
        leave, so the drain must leave it untouched."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.subscribe_room("room-1")
        await link.subscribe_room("room-2")

        await link._on_reconnected()

        assert link.is_room_subscribed("room-1") is True
        assert link.is_room_subscribed("room-2") is True
        mock_ws_client.leave_chat_room_channel.assert_not_called()
        mock_ws_client.leave_room_participants_channel.assert_not_called()

    async def test_disconnect_when_not_connected_is_noop(self):
        """disconnect() when not connected should be a no-op."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.disconnect()  # Should not raise

        assert link.is_connected is False

    @pytest.mark.parametrize(
        ("kwargs", "installs"),
        INSTALL_SIGNAL_HANDLER_CASES,
        ids=INSTALL_SIGNAL_HANDLER_IDS,
    )
    @patch("band.platform.link.WebSocketClient")
    async def test_run_forever_delegates_to_websocket(
        self, mock_ws_class, mock_ws_client, kwargs, installs
    ):
        """run_forever() hands the host's signal choice to WebSocket."""
        mock_ws_class.return_value = mock_ws_client

        link = BandLink(agent_id="agent-123", api_key="test-key")
        await link.connect()
        await link.run_forever(**kwargs)

        mock_ws_client.run_forever.assert_awaited_once_with(
            install_signal_handlers=installs
        )

    async def test_run_forever_raises_when_not_connected(self):
        """run_forever() should raise RuntimeError when not connected."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        with pytest.raises(RuntimeError, match="Not connected"):
            await link.run_forever()

    async def test_close_without_supersede_leaves_disconnect_reason_empty(self):
        """An empty Phoenix close should not invent a terminal reason."""
        link = BandLink(agent_id="agent-123", api_key="test-key")

        await link._on_disconnected(None)

        assert link.last_disconnect_reason is None
        assert link._event_queue.empty()
