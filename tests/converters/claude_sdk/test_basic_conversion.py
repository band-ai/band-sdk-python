"""Tests for ClaudeSDK-specific message conversion to text."""

from __future__ import annotations

from band.converters.claude_sdk import (
    ClaudeSDKHistoryConverter,
    ClaudeSDKSessionState,
)


class TestBasicConversion:
    def test_converts_multiple_messages(self):
        """Multiple messages are joined with newlines."""
        converter = ClaudeSDKHistoryConverter()
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

        expected = "[Alice]: Hello!\n[Bob]: Hi there!"
        assert result.text == expected

    def test_returns_session_state(self):
        """convert() returns a ClaudeSDKSessionState instance."""
        converter = ClaudeSDKHistoryConverter()
        result = converter.convert([])
        assert isinstance(result, ClaudeSDKSessionState)
