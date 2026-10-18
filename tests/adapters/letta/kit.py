from __future__ import annotations

import json

import pytest

from band.core.types import ToolEventKey
from band.runtime.tools import BandTool
from band.testing import (
    FakeAgentTools,
    events_of_type,
)

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


def reported_tool_names(tools: FakeAgentTools, event_type: str) -> list[str]:
    return [
        json.loads(event["content"])[ToolEventKey.NAME]
        for event in events_of_type(tools, event_type)
    ]
