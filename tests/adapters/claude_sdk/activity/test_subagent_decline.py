"""A native subagent's decline ends its own Band calls, not the main model's."""

from __future__ import annotations

import asyncio

from band.runtime.tools import MCP_TOOL_PREFIX, BandTool
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.adapters.claude_sdk.activity.kit import (
    native_task_started,
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.helpers import ClaudeRoom, OpenRoom
from tests.adapters.hold import Hold
from tests.baseline.decisions import ToolCall
from tests.mcp.toolresults import content_blocks, mcp_tool_error

AGENT_CALL, SUBAGENT_TASK = "agent-call", "agent-task"
MAIN_REPLY = "Here is the answer."
SUBAGENT_DECLINE = ToolCall(
    f"{MCP_TOOL_PREFIX}{BandTool.NO_REPLY}", {"reason": "nothing to add"}
)
SUBAGENT_REPLY = ToolCall(
    f"{MCP_TOOL_PREFIX}{BandTool.SEND_MESSAGE}",
    {"content": "Subagent findings.", "mentions": ["@bob"]},
)


def script_subagent_then_reply(room: ClaudeRoom, hold: Hold) -> None:
    """The model starts a subagent, which runs while ``hold`` is held, then
    the model answers the room itself."""
    room.claude.script(
        [
            native_tool_uses((AGENT_CALL, "Agent")),
            native_task_started(SUBAGENT_TASK, AGENT_CALL),
            hold,
            native_tool_result(AGENT_CALL),
            room.model_reply(MAIN_REPLY),
        ]
    )


class TestSubagentDecline:
    async def test_the_main_model_still_replies_after_its_subagent_declines(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(emit=())
        hold = Hold()
        script_subagent_then_reply(room, hold)

        async with asyncio.TaskGroup() as group:
            group.create_task(room.send("Research this"))
            async with hold:
                await room.claude.sessions[-1].background_tool(
                    SUBAGENT_TASK, "sub-decline", SUBAGENT_DECLINE
                )

        assert room.chat == [MAIN_REPLY]

    async def test_the_subagents_own_later_call_is_refused(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(emit=())
        hold = Hold()
        script_subagent_then_reply(room, hold)

        async with asyncio.TaskGroup() as group:
            group.create_task(room.send("Research this"))
            async with hold:
                subagent = room.claude.sessions[-1]
                await subagent.background_tool(
                    SUBAGENT_TASK, "sub-decline", SUBAGENT_DECLINE
                )
                late = await subagent.background_tool_result(
                    SUBAGENT_TASK, "sub-late", SUBAGENT_REPLY
                )

        refusal = mcp_tool_error(BandTool.SEND_MESSAGE, DECLINED_TURN_REFUSAL)
        assert late == (content_blocks(refusal), True)
        assert room.chat == [MAIN_REPLY]
