"""Fake Parlant agent events for the ParlantAdapter tests."""

from __future__ import annotations

from unittest.mock import MagicMock

MESSAGE_KIND = "message"
AI_AGENT_SOURCE = "ai_agent"
# The session the mock Application creates, and who sends the sample message.
SESSION_ID = "session-123"
SENDER_NAME = "Alice"


def agent_event(
    message: str, *, offset: int, tags: list[str] | None = None
) -> MagicMock:
    """A Parlant AI-agent MESSAGE event as the response wait reads it."""
    event = MagicMock()
    event.kind = MESSAGE_KIND
    event.source = AI_AGENT_SOURCE
    event.offset = offset
    event.data = {"message": message, "tags": tags or []}
    return event
