"""ConcurrentMessages behavior of the Google ADK adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from band.core.types import PlatformMessage
from tests.adapters.googleadk.bridge import GoogleADKAdapter
from tests.adapters.googleadk.kit import _empty_async_iter


class TestConcurrentMessages:
    """Tests for concurrent on_message calls."""

    @pytest.mark.asyncio
    async def test_concurrent_messages_different_rooms(self, mock_tools):
        """Concurrent on_message calls on different rooms should not interfere."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        msg_a = PlatformMessage(
            id="msg-a",
            room_id="room-a",
            content="Hello from room A",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        msg_b = PlatformMessage(
            id="msg-b",
            room_id="room-b",
            content="Hello from room B",
            sender_id="user-2",
            sender_type="User",
            sender_name="Bob",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await asyncio.gather(
                adapter.on_message(
                    msg=msg_a,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-a",
                ),
                adapter.on_message(
                    msg=msg_b,
                    tools=mock_tools,
                    history=[],
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-b",
                ),
            )

        # Each room should have its own independent history
        assert "room-a" in adapter._room_history
        assert "room-b" in adapter._room_history
        assert len(adapter._room_history["room-a"]) == 1
        assert len(adapter._room_history["room-b"]) == 1
        assert "room A" in adapter._room_history["room-a"][0]["content"]
        assert "room B" in adapter._room_history["room-b"][0]["content"]

        # Each room should have its own session
        assert adapter._room_sessions["room-a"] != adapter._room_sessions["room-b"]
