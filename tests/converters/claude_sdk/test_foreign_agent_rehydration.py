"""A different-framework peer's fact must survive into the rehydrated text.

Cross-framework recall (agent A rehydrates a fact a *peer* agent B authored while
A was offline) depends on B's line reaching A's history: kept iff it is not A's
own text. Regression guard for the ``/context`` recall path.
"""

from __future__ import annotations

from band.converters.claude_sdk import (
    ClaudeSDKHistoryConverter,
)


class TestForeignAgentRehydration:
    def test_keeps_peer_agent_line_drops_own(self):
        converter = ClaudeSDKHistoryConverter()
        converter.set_agent_name("Me")
        raw = [
            {
                "content": "the note is note-DEADBEEF",
                "role": "assistant",
                "sender_name": "speaker-langgraph",  # a peer agent, not Me
                "message_type": "text",
            },
            {
                "content": "my own reply",
                "role": "assistant",
                "sender_name": "Me",  # this agent's own text — dropped
                "message_type": "text",
            },
        ]

        text = converter.convert(raw).text

        assert "[speaker-langgraph]: the note is note-DEADBEEF" in text
        assert "my own reply" not in text
