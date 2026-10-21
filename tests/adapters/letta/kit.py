"""Shared helpers for the Letta adapter tests."""

from __future__ import annotations

import pytest

from band.runtime.tools import BandTool
from band.testing import FakeAgentTools

# Band tool calls that settle the turn's reply, so no assistant text is relayed.
REPLY_SETTLING_CALLS = [
    pytest.param(
        BandTool.SEND_MESSAGE, {"content": "Hi", "mentions": ["@alice"]}, id="message"
    ),
    pytest.param(
        BandTool.SEND_ROOM_FILE,
        {"content": "notes", "filename": "notes.txt", "mentions": ["@alice"]},
        id="room-file",
    ),
    pytest.param(BandTool.NO_REPLY, {"reason": "FYI only"}, id="no-reply"),
]


def sent_contents(tools: FakeAgentTools) -> list[str]:
    return [message["content"] for message in tools.messages_sent]
