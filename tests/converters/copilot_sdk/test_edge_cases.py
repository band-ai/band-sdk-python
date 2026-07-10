"""CopilotSDK-specific edge cases not covered by conformance tests.

The conformance test ``test_defaults_to_user_role`` skips CopilotSDK
because ``has_role_concept=False`` (output is a flat string).  This
module covers the missing-role behavior directly.
"""

from __future__ import annotations

from band.converters.copilot_sdk import CopilotSDKHistoryConverter


class TestEdgeCases:
    def test_missing_role_defaults_to_user(self):
        """Messages without a 'role' key are treated as user messages."""
        converter = CopilotSDKHistoryConverter(agent_name="MyAgent")
        raw = [
            {
                "content": "Hello",
                "sender_name": "Bob",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert "[Bob]: Hello" in result.text

    def test_includes_own_agent_messages(self):
        """Own replies are kept: they are the only record of the agent's side
        (band_send_message tool reporting is silenced by the adapter)."""
        converter = CopilotSDKHistoryConverter(agent_name="MyAgent")
        raw = [
            {
                "role": "assistant",
                "content": "Here's my answer",
                "sender_name": "MyAgent",
                "message_type": "text",
            }
        ]

        result = converter.convert(raw)

        assert result.text == "[MyAgent]: Here's my answer"

    def test_unknown_message_type_skipped(self):
        """Unknown message types (e.g. thought) never reach the text blob."""
        converter = CopilotSDKHistoryConverter()
        raw = [
            {
                "content": "internal reasoning",
                "sender_name": "MyAgent",
                "message_type": "thought",
            },
            {
                "content": "Hello",
                "sender_name": "Alice",
                "message_type": "text",
            },
        ]

        result = converter.convert(raw)

        assert result.text == "[Alice]: Hello"
