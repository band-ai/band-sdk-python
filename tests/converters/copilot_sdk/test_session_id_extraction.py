"""Tests for session_id extraction from task events."""

from __future__ import annotations

from band.converters.copilot_sdk import CopilotSDKHistoryConverter


class TestSessionIdExtraction:
    def test_extracts_session_id_from_task_event(self):
        """Should extract session_id from task event metadata."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "content": "Copilot SDK session",
                "message_type": "task",
                "metadata": {"copilot_session_id": "band-room-abc"},
            },
        ]

        result = converter.convert(raw)

        assert result.session_id == "band-room-abc"

    def test_returns_latest_session_id(self):
        """When multiple task events exist, return the latest session_id."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "content": "Copilot SDK session",
                "message_type": "task",
                "metadata": {"copilot_session_id": "sess-old"},
            },
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "content": "Copilot SDK session",
                "message_type": "task",
                "metadata": {"copilot_session_id": "sess-new"},
            },
        ]

        result = converter.convert(raw)

        assert result.session_id == "sess-new"

    def test_returns_none_when_no_task_events(self):
        """Should return None when no task events exist."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert result.session_id is None

    def test_ignores_task_events_without_session_id_key(self):
        """Should ignore task events that don't have copilot_session_id."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "content": "Some other task event",
                "message_type": "task",
                "metadata": {"other_key": "value"},
            },
        ]

        result = converter.convert(raw)

        assert result.session_id is None

    def test_tolerates_task_events_with_missing_metadata(self):
        """Task events without metadata (or metadata=None) are skipped."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {"content": "junk", "message_type": "task"},
            {"content": "junk", "message_type": "task", "metadata": None},
            {
                "content": "Copilot SDK session",
                "message_type": "task",
                "metadata": {"copilot_session_id": "sess-good"},
            },
            {"content": "junk", "message_type": "task", "metadata": {}},
        ]

        result = converter.convert(raw)

        assert result.session_id == "sess-good"

    def test_task_events_not_included_in_text(self):
        """Task events should never appear in the text output."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "content": "Copilot SDK session",
                "message_type": "task",
                "metadata": {"copilot_session_id": "sess-abc"},
            },
        ]

        result = converter.convert(raw)

        assert "Copilot SDK session" not in result.text
        assert "sess-abc" not in result.text
        assert result.text == "[Alice]: Hello"

    def test_empty_history_returns_empty_state(self):
        """Empty history should return empty state with no session_id."""
        converter = CopilotSDKHistoryConverter()

        result = converter.convert([])

        assert result.text == ""
        assert result.session_id is None
