"""Read adapter tool telemetry as structured records."""

from __future__ import annotations

import json

from band.core.types import MessageType
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type


def decoded_events(tools: FakeAgentTools, message_type: str) -> list[dict[str, object]]:
    return [
        json.loads(event["content"]) for event in events_of_type(tools, message_type)
    ]


def tool_call_records(tools: FakeAgentTools) -> list[dict[str, object]]:
    return decoded_events(tools, MessageType.TOOL_CALL)


def tool_result_records(tools: FakeAgentTools) -> list[dict[str, object]]:
    return decoded_events(tools, MessageType.TOOL_RESULT)
