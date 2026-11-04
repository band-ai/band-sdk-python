"""TranscriptTruncation behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter, GoogleADKAdapterConfig
from tests.adapters.googleadk.kit import _empty_async_iter


class TestTranscriptTruncation:
    """Tests for character-based transcript truncation."""

    @pytest.mark.asyncio
    async def test_truncates_large_transcript(self, sample_message, mock_tools):
        """Should truncate transcript exceeding max_transcript_chars."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_transcript_chars=200))
        await adapter.on_started("TestBot", "Test bot")

        # Seed history with messages that produce a transcript > 200 chars
        adapter._room_history["room-123"] = [
            {"role": "user", "content": f"[User]: Message number {i} with padding"}
            for i in range(20)
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
        # The transcript portion (between the markers) should be <= 200 chars
        start = text.find("[Previous conversation context]\n")
        end = text.find("\n[End of previous context]")
        if start != -1 and end != -1:
            transcript_section = text[
                start + len("[Previous conversation context]\n") : end
            ]
            assert len(transcript_section) <= 200
        # Older messages should be dropped, recent ones kept
        assert "Message number 19" in text
        assert "Message number 0" not in text

    @pytest.mark.asyncio
    async def test_no_truncation_when_within_limit(self, sample_message, mock_tools):
        """Should not truncate transcript when within max_transcript_chars."""
        adapter = GoogleADKAdapter(GoogleADKAdapterConfig(max_transcript_chars=100_000))
        await adapter.on_started("TestBot", "Test bot")

        adapter._room_history["room-123"] = [
            {"role": "user", "content": "[User]: Short msg"},
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
        assert "[User]: Short msg" in text
