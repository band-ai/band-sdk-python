"""Shared fakes and sample values for the ParlantAdapter tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from pydantic import BaseModel

MESSAGE_KIND = "message"
AI_AGENT_SOURCE = "ai_agent"
# The session the mock Application creates, and who sends the sample message.
SESSION_ID = "session-123"
SENDER_NAME = "Alice"
# The Band agent's own name and description, handed to on_started.
BAND_NAME = "BandName"
BAND_DESCRIPTION = "Band description"


class LookupInput(BaseModel):
    """Look a code up."""

    code: str


async def lookup(args: LookupInput) -> str:
    return args.code


LOOKUP = (LookupInput, lookup)


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
