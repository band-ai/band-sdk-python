"""A turn that replies over Band's MCP server, then runs a native tool."""

from __future__ import annotations

from typing import Any

from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, shown_tool
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
