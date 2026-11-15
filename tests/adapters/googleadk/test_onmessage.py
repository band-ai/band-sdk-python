"""OnMessage behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter
from tests.adapters.googleadk.kit import _empty_async_iter


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_creates_session_on_bootstrap(self, sample_message, mock_tools):
        """Should create a new session on first message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert "room-123" in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_creates_new_session_on_each_message(
        self, sample_message, mock_tools
    ):
        """Should create a fresh session per message (runner is ephemeral)."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = _empty_async_iter
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            # First message (bootstrap)
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            session_id_1 = adapter._room_sessions["room-123"]

            # Second message (not bootstrap)
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

            session_id_2 = adapter._room_sessions["room-123"]
            assert session_id_1 != session_id_2

    @pytest.mark.asyncio
    async def test_injects_participants_message(self, sample_message, mock_tools):
        """Should include participants update in message."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg="Alice joined the room",
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        content = captured_messages[0]
        text = content.parts[0].text
        assert "[System]: Alice joined the room" in text

    @pytest.mark.asyncio
    async def test_injects_history_on_bootstrap(self, sample_message, mock_tools):
        """Should inject history transcript on bootstrap."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        history = [
            {"role": "user", "content": "[Bob]: Previous message"},
            {"role": "model", "content": "Previous response"},
        ]

        captured_messages = []

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()

            def capture_run(**kwargs):
                captured_messages.append(kwargs.get("new_message"))
                return _empty_async_iter()

            mock_runner.run_async = capture_run
            mock_runner.close = AsyncMock()
            mock_create.return_value = mock_runner

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=history,
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert len(captured_messages) == 1
        text = captured_messages[0].parts[0].text
        assert "Previous conversation context" in text
        assert "[Bob]: Previous message" in text
