"""Tests that on_reconnected fires reliably from _handle_reconnect."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.presence import RoomPresence
from tests.runtime.conftest import admit_room


class TestPresenceReconnectOnReconnectedCallback:
    """Tests that on_reconnected fires reliably from _handle_reconnect."""

    @pytest.fixture
    def mock_presence_link(self):
        link = MagicMock()
        link.agent_id = "agent-123"
        link.is_connected = False
        link.connect = AsyncMock()
        link.subscribe_agent_rooms = AsyncMock()
        link.subscribe_room = AsyncMock()
        link.unsubscribe_room = AsyncMock()

        link.rest = MagicMock()
        link.rest.agent_api_chats = MagicMock()

        # Return a properly structured response so _list_existing_rooms terminates
        # correctly (total_pages=None breaks the pagination loop after one call).
        api_response = MagicMock()
        api_response.data = []
        api_response.metadata = MagicMock()
        api_response.metadata.total_pages = None
        link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=api_response
        )

        async def empty_aiter():
            return
            yield

        link.__aiter__ = lambda self: empty_aiter()
        return link

    async def test_on_reconnected_fires_with_auto_subscribe_true(
        self, mock_presence_link
    ):
        """on_reconnected fires after reconnect when auto_subscribe_existing=True."""
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_fires_with_auto_subscribe_false(
        self, mock_presence_link
    ):
        """on_reconnected fires after reconnect even when auto_subscribe_existing=False.

        The finally block ensures the callback always fires regardless of early
        returns in the try block (which exits early when auto_subscribe_existing=False).
        """
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=False)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_still_fires_if_api_fails(self, mock_presence_link):
        """on_reconnected should still fire if room reconciliation fails.

        Existing executions still need a /next resync even when the room-list API
        is temporarily unavailable during reconnect.
        """
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        mock_presence_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_on_reconnected_still_fires_if_unsubscribe_fails(
        self, mock_presence_link
    ):
        """on_reconnected should still fire if a stale room unsubscribe fails."""
        reconnected_calls = []

        async def on_reconnected():
            reconnected_calls.append(1)

        room = MagicMock()
        room.id = "room-new"
        room.model_dump.return_value = {"id": "room-new"}

        api_response = MagicMock()
        api_response.data = [room]
        api_response.metadata = MagicMock()
        api_response.metadata.total_pages = None
        mock_presence_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=api_response
        )
        mock_presence_link.unsubscribe_room = AsyncMock(
            side_effect=RuntimeError("unsubscribe failed")
        )

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=True)
        admit_room(presence, "room-old")
        presence.on_reconnected = on_reconnected

        await presence._handle_reconnect()

        assert len(reconnected_calls) == 1

    async def test_cancelled_error_propagates_from_callback(self, mock_presence_link):
        """CancelledError raised in on_reconnected must propagate (structured concurrency)."""

        async def on_reconnected_that_raises():
            raise asyncio.CancelledError

        presence = RoomPresence(mock_presence_link, auto_subscribe_existing=False)
        presence.on_reconnected = on_reconnected_that_raises

        with pytest.raises(asyncio.CancelledError):
            await presence._handle_reconnect()
