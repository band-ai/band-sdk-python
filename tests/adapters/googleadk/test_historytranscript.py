"""HistoryTranscript behavior of the Google ADK adapter."""

from __future__ import annotations

import pytest

from tests.adapters.googleadk.bridge import GoogleADKAdapter


class TestHistoryTranscript:
    """Tests for _format_history_transcript."""

    def test_formats_text_messages(self):
        """Should format text messages."""
        adapter = GoogleADKAdapter()
        history = [
            {"role": "user", "content": "[Alice]: Hello"},
            {"role": "user", "content": "[Bob]: Hi there"},
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Alice]: Hello" in transcript
        assert "[Bob]: Hi there" in transcript

    def test_formats_tool_calls(self):
        """Should format tool call blocks."""
        adapter = GoogleADKAdapter()
        history = [
            {
                "role": "model",
                "content": [
                    {
                        "type": "function_call",
                        "name": "band_send_message",
                        "args": {"content": "Hello"},
                    }
                ],
            }
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Tool Call]" in transcript
        assert "band_send_message" in transcript

    def test_formats_tool_results(self):
        """Should format tool result blocks."""
        adapter = GoogleADKAdapter()
        history = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "function_response",
                        "name": "band_send_message",
                        "output": '{"status": "sent"}',
                    }
                ],
            }
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Tool Result]" in transcript
        assert "band_send_message" in transcript

    def test_empty_history(self):
        """Should return empty string for empty history."""
        adapter = GoogleADKAdapter()
        assert adapter._format_history_transcript([]) == ""

    @pytest.mark.asyncio
    async def test_labels_own_agent_replies_with_agent_name(self):
        """Own-agent text (role='model', bare content) should be labeled with the agent's name.

        INT-509 regression: without a speaker label, rehydrated transcripts
        showed the agent's prior replies as anonymous lines and the LLM
        could not tell them apart from peer turns, leading it to re-answer
        questions it had already handled after a restart.

        ``SimpleAdapter.on_started`` propagates the agent name into the
        converter, which is the single source of truth for own-vs-peer
        attribution.  This test exercises that wiring end-to-end so a
        future refactor that drops the propagation step would fail here.
        """
        adapter = GoogleADKAdapter()
        await adapter.on_started("ResearchBot", "Test bot")
        history = [
            {"role": "user", "content": "[Alice]: What's the capital of France?"},
            {"role": "model", "content": "Paris."},
            {"role": "user", "content": "[Alice]: Remember pineapple"},
            {"role": "model", "content": "I'll remember 'pineapple'."},
        ]

        transcript = adapter._format_history_transcript(history)

        assert "[Alice]: What's the capital of France?" in transcript
        assert "[ResearchBot]: Paris." in transcript
        assert "[ResearchBot]: I'll remember 'pineapple'." in transcript
