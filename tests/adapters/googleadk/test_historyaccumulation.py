"""HistoryAccumulation behavior of the Google ADK adapter."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter, GoogleADKAdapterConfig
from tests.adapters.googleadk.kit import _empty_async_iter


class TestHistoryAccumulation:
    """Tests for per-room history accumulation across messages."""

    @pytest.mark.asyncio
    async def test_non_bootstrap_uses_accumulated_history(
        self, sample_message, mock_tools
    ):
        """Non-bootstrap messages should still inject accumulated history."""
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

            # First message (bootstrap) seeds history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[
                    {"role": "user", "content": "[Bob]: Previous message"},
                ],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Second message (non-bootstrap) should still have history
            await adapter.on_message(
                msg=replace(sample_message, id="next-message"),
                tools=mock_tools,
                history=[],  # History param ignored on non-bootstrap
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # Second message should contain accumulated history
        assert len(captured_messages) == 2
        text = captured_messages[1].parts[0].text
        assert "Previous conversation context" in text
        # Should also have the first user message accumulated
        assert "Hello, agent!" in text

    @pytest.mark.asyncio
    async def test_non_bootstrap_no_history_injection_when_empty(
        self, sample_message, mock_tools
    ):
        """Non-bootstrap with no prior history should not inject transcript."""
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

            # Bootstrap with empty history
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Non-bootstrap - accumulated history only has the first msg
            await adapter.on_message(
                msg=replace(sample_message, id="next-message"),
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # Second message should have context from first message
        text = captured_messages[1].parts[0].text
        assert "Previous conversation context" in text

    @pytest.mark.asyncio
    async def test_cleanup_removes_room_history(self):
        """on_cleanup should remove accumulated history."""
        adapter = GoogleADKAdapter()
        adapter._room_history["room-123"] = [
            {"role": "user", "content": "test"},
        ]
        adapter._room_sessions["room-123"] = "session-abc"

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._room_history
        assert "room-123" not in adapter._room_sessions

    @pytest.mark.asyncio
    async def test_sliding_window_limits_transcript(self, sample_message, mock_tools):
        """Should only inject the last max_history_messages in the transcript."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_history_messages=3))
        await adapter.on_started("TestBot", "Test bot")

        # Seed room history with more messages than the window
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message {i}"} for i in range(10)
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
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

        text = captured_messages[0].parts[0].text
        # Only the last 3 messages should be in the transcript
        assert "Message 7" in text
        assert "Message 8" in text
        assert "Message 9" in text
        # Older messages should be excluded
        assert "Message 0" not in text
        assert "Message 6" not in text

    @pytest.mark.asyncio
    async def test_history_trimmed_to_prevent_unbounded_growth(
        self, sample_message, mock_tools
    ):
        """Should trim accumulated history when it exceeds 2x max_history_messages."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_history_messages=3))
        await adapter.on_started("TestBot", "Test bot")

        # Seed room history just below the trim threshold (2 * 3 = 6)
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message {i}"} for i in range(6)
        ]

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
                is_session_bootstrap=False,
                room_id="room-123",
            )

        # 6 seeded + 1 new user message = 7 > threshold (6), triggers trim to 3
        history = adapter._room_history["room-123"]
        assert len(history) == 3
        # Should keep the most recent messages
        assert history[-1]["role"] == "user"
        assert history[-1]["content"] == "[Alice]: Hello, agent!"
