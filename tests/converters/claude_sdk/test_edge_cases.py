"""ClaudeSDK-specific edge cases not covered by conformance tests.

The conformance test ``test_defaults_to_user_role`` skips ClaudeSDK
because ``has_role_concept=False`` (output is a flat string).  This
module covers the missing-role behavior directly.
"""

from __future__ import annotations

from band.converters.claude_sdk import (
    ClaudeSDKHistoryConverter,
)


class TestEdgeCases:
    def test_missing_role_defaults_to_user(self):
        """Messages without a 'role' key are treated as user messages."""
        converter = ClaudeSDKHistoryConverter(agent_name="MyAgent")
        raw = [
            {
                "content": "Hello",
                "sender_name": "Bob",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        # Should be included (not filtered as own-agent message)
        assert "[Bob]: Hello" in result.text

    def test_missing_role_not_filtered_as_assistant(self):
        """Messages without 'role' must not be filtered by agent_name."""
        converter = ClaudeSDKHistoryConverter(agent_name="MyAgent")
        raw = [
            {
                "content": "I said something",
                "sender_name": "MyAgent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        # Without "role": "assistant", the message defaults to user role
        # and should NOT be filtered even though sender_name matches agent_name.
        assert "I said something" in result.text
