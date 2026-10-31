"""Claude turns that run Band and native tools, as the CLI streams them."""

from __future__ import annotations

from typing import Any

from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, shown_tool
from tests.adapters.claude_sdk.fakecli import MODEL, Raw
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.baseline.decisions import ModelDecision

# The reply's MCP call ends before the CLI streams the Bash call, so the order
# is fixed.
REPLY_THEN_BASH_ACTIVITY: list[dict[str, Any]] = [
    shown_tool(BandTool.SEND_MESSAGE),
    SHOWN_REASONING,
    shown_tool("Bash"),
    SHOWN_REASONING,
]


def reply_then_bash(room: ClaudeRoom) -> list[ModelDecision]:
    return [room.model_reply("Listing."), ModelDecision.call("Bash", command="ls")]


def native_tool_uses(*calls: tuple[str, str]) -> Raw:
    """One assistant message starting each ``(tool_use_id, name)`` call."""
    blocks = [
        {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}
        for tool_use_id, name in calls
    ]
    return Raw({"type": "assistant", "message": {"model": MODEL, "content": blocks}})


def native_tool_result(tool_use_id: str) -> Raw:
    """The CLI's result for the native call ``tool_use_id``."""
    block = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": "ran",
        "is_error": False,
    }
    return Raw({"type": "user", "message": {"content": [block]}})
