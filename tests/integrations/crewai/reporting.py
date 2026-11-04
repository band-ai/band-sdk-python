"""Expected CrewAI tool-failure reporting events."""

from __future__ import annotations

import json
from typing import Any

from band.core.types import MessageType, ToolEventKey
from band.runtime.custom_tools import get_custom_tool_name
from tests.adapters.crewaiturnend.kit import DeclineInput


def failed_tool_events(raw: Any) -> list[dict[str, Any]]:
    """Describe the delivered call and failed result for a custom decline."""
    name = get_custom_tool_name(DeclineInput)
    return [
        {
            "id": "evt-0",
            "content": json.dumps(
                {ToolEventKey.NAME: name, ToolEventKey.ARGS: {"reason": "try"}}
            ),
            "message_type": MessageType.TOOL_CALL,
            "metadata": {},
        },
        {
            "id": "evt-1",
            "content": json.dumps(
                {
                    ToolEventKey.NAME: name,
                    ToolEventKey.OUTPUT: raw,
                    ToolEventKey.IS_ERROR: True,
                }
            ),
            "message_type": MessageType.TOOL_RESULT,
            "metadata": {},
        },
    ]
