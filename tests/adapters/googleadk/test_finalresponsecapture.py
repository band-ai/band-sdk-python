"""FinalResponseCapture behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter


class TestFinalResponseCapture:
    """Tests for capturing final text response from ADK events."""

    @pytest.mark.asyncio
    async def test_captures_final_response_in_history(self, sample_message, mock_tools):
        """Should capture final response text and add to room history."""
        adapter = GoogleADKAdapter()
        await adapter.on_started("TestBot", "Test bot")

        mock_event = MagicMock()
        mock_event.is_final_response.return_value = True
        mock_event.error_code = None
        mock_part = MagicMock()
        mock_part.text = "Here is my response"
        mock_event.content.parts = [mock_part]

        async def iter_with_response(**kwargs):
            yield mock_event

        with patch.object(adapter, "_create_runner") as mock_create:
            mock_runner = AsyncMock()
            mock_runner.run_async = iter_with_response
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

        # History should contain both user message and model response
        history = adapter._room_history["room-123"]
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "model"
        assert history[1]["content"] == "Here is my response"

    def test_extract_event_text_with_content(self):
        """Should extract text from event content parts."""
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = "response text"
        mock_event.content.parts = [mock_part]

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == "response text"

    def test_extract_event_text_no_content(self):
        """Should return empty string when event has no content."""
        mock_event = MagicMock()
        mock_event.content = None

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == ""

    def test_extract_event_text_no_text_parts(self):
        """Should return empty string when parts have no text."""
        mock_event = MagicMock()
        mock_part = MagicMock()
        mock_part.text = None
        mock_event.content.parts = [mock_part]

        result = GoogleADKAdapter._extract_event_text(mock_event)
        assert result == ""
