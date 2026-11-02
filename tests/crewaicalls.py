"""Native tool calls in the shape a scripted CrewAI model returns them."""

from __future__ import annotations

import json
from typing import Any


def native_tool_call(
    call_id: str, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """One function call as CrewAI's native tool path parses it."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
