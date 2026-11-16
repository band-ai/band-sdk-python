"""Claude turns that run Band and native tools, as the CLI streams them."""

from __future__ import annotations

from typing import Any

from band.runtime.tools import MCP_TOOL_PREFIX
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, shown_tool
from tests.adapters.claude_sdk.fakecli import MODEL, Raw
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.baseline.decisions import ModelDecision, ToolCall
from tests.mcp.toolresults import content_blocks, mcp_tool_error

BACKGROUND_CALL = "background-call"
BACKGROUND_TASK = "background-task"
#: The model's native ``Agent`` call and the subagent task it starts.
AGENT_CALL, SUBAGENT_TASK = "agent-call", "agent-task"

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


def native_task_started(task_id: str, call_id: str) -> Raw:
    return Raw(
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": task_id,
            "tool_use_id": call_id,
            "description": "private task description",
            "uuid": "started",
            "session_id": "session-1",
        }
    )


#: A native subagent's Band calls, as the CLI names them on the Band server.
SUBAGENT_DECLINE = ToolCall(
    f"{MCP_TOOL_PREFIX}{BandTool.NO_REPLY}", {"reason": "nothing to add"}
)
SUBAGENT_REPLY = ToolCall(
    f"{MCP_TOOL_PREFIX}{BandTool.SEND_MESSAGE}",
    {"content": "Subagent findings.", "mentions": ["@bob"]},
)
#: What the CLI hands a refused subagent ``band_send_message``.
REFUSED_SUBAGENT_REPLY = (
    content_blocks(mcp_tool_error(BandTool.SEND_MESSAGE, DECLINED_TURN_REFUSAL)),
    True,
)
