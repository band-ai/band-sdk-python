"""Tests for ACPPushHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from band.integrations.acp.push_handler import ACPPushHandler
from band.integrations.acp.server_adapter import BandACPServerAdapter

from .conftest import make_platform_message


class TestACPPushHandler:
    """Tests for ACPPushHandler.handle_push_event()."""

    @pytest.mark.asyncio
    async def test_push_sends_session_update(self) -> None:
        """Should send session_update for mapped room with ACP client."""
        adapter = BandACPServerAdapter()
        mock_client = AsyncMock()
        mock_client.session_update = AsyncMock()
        adapter._acp_client = mock_client
        adapter._room_to_session["room-123"] = "session-abc"

        handler = ACPPushHandler(adapter)
        msg = make_platform_message("New activity", room_id="room-123")

        with patch(
            "band.integrations.acp.push_handler.EventConverter"
        ) as mock_converter:
            mock_converter.convert.return_value = "mock-chunk"
            await handler.handle_push_event(msg, "room-123")

        mock_client.session_update.assert_called_once_with(
            session_id="session-abc",
            update="mock-chunk",
        )

    @pytest.mark.asyncio
    async def test_push_uses_event_converter(self) -> None:
        """Should convert message via EventConverter."""
        adapter = BandACPServerAdapter()
        mock_client = AsyncMock()
        adapter._acp_client = mock_client
        adapter._room_to_session["room-123"] = "session-abc"

        handler = ACPPushHandler(adapter)
        msg = make_platform_message(
            "Thinking...", room_id="room-123", message_type="thought"
        )

        with patch(
            "band.integrations.acp.push_handler.EventConverter"
        ) as mock_converter:
            mock_converter.convert.return_value = "thought-chunk"
            await handler.handle_push_event(msg, "room-123")

        mock_converter.convert.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_push_no_client_no_crash(self) -> None:
        """Should handle gracefully when no ACP client is connected."""
        adapter = BandACPServerAdapter()
        adapter._room_to_session["room-123"] = "session-abc"
        # No _acp_client set

        handler = ACPPushHandler(adapter)
        msg = make_platform_message("Hello", room_id="room-123")

        # Should not raise
        await handler.handle_push_event(msg, "room-123")

    @pytest.mark.asyncio
    async def test_push_no_session_mapping_skips(self) -> None:
        """Should skip when room has no session mapping."""
        adapter = BandACPServerAdapter()
        mock_client = AsyncMock()
        adapter._acp_client = mock_client

        handler = ACPPushHandler(adapter)
        msg = make_platform_message("Hello", room_id="unmapped-room")

        await handler.handle_push_event(msg, "unmapped-room")

        mock_client.session_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_none_chunk_not_sent(self) -> None:
        """Should not send when EventConverter returns None."""
        adapter = BandACPServerAdapter()
        mock_client = AsyncMock()
        adapter._acp_client = mock_client
        adapter._room_to_session["room-123"] = "session-abc"

        handler = ACPPushHandler(adapter)
        msg = make_platform_message("Hello", room_id="room-123")

        with patch(
            "band.integrations.acp.push_handler.EventConverter"
        ) as mock_converter:
            mock_converter.convert.return_value = None
            await handler.handle_push_event(msg, "room-123")

        mock_client.session_update.assert_not_called()
