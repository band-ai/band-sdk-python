"""Tests for CopilotSDK-specific message conversion to text."""

from __future__ import annotations

from band.converters.copilot_sdk import (
    CopilotSDKHistoryConverter,
    CopilotSDKSessionState,
)


class TestBasicConversion:
    def test_converts_multiple_messages(self):
        """Multiple messages are joined with newlines."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "role": "user",
                "content": "Hello!",
                "sender_name": "Alice",
                "message_type": "text",
            },
            {
                "role": "user",
                "content": "Hi there!",
                "sender_name": "Bob",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert result.text == "[Alice]: Hello!\n[Bob]: Hi there!"

    def test_returns_session_state(self):
        """convert() returns a CopilotSDKSessionState instance."""
        converter = CopilotSDKHistoryConverter()
        result = converter.convert([])
        assert isinstance(result, CopilotSDKSessionState)
