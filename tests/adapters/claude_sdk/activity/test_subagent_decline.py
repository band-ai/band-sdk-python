"""A native subagent's decline ends its own Band calls only."""

from __future__ import annotations

import asyncio

from tests.adapters.claude_sdk.activity.kit import (
    AGENT_CALL,
    REFUSED_SUBAGENT_REPLY,
    SUBAGENT_DECLINE,
    SUBAGENT_REPLY,
    SUBAGENT_TASK,
    native_task_started,
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.helpers import ClaudeRoom, OpenRoom
from tests.adapters.hold import Hold

OTHER_CALL, OTHER_TASK = "other-call", "other-task"
MAIN_REPLY = "Here is the answer."


def script_subagents_then_reply(
    room: ClaudeRoom, hold: Hold, *subagents: tuple[str, str]
) -> None:
    """The model starts each ``(call, task)`` subagent, which run while
    ``hold`` is held, then the model answers the room itself."""
    room.claude.script(
        [
            native_tool_uses(*((call, "Agent") for call, _ in subagents)),
            *(native_task_started(task, call) for call, task in subagents),
            hold,
            *(native_tool_result(call) for call, _ in subagents),
            room.model_reply(MAIN_REPLY),
        ]
    )


class TestSubagentDecline:
    async def test_the_main_model_still_replies_after_its_subagent_declines(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(emit=())
        hold = Hold()
        script_subagents_then_reply(room, hold, (AGENT_CALL, SUBAGENT_TASK))

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
        script_subagents_then_reply(room, hold, (AGENT_CALL, SUBAGENT_TASK))

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

        assert late == REFUSED_SUBAGENT_REPLY
        assert room.chat == [MAIN_REPLY]

    async def test_a_sibling_subagent_still_posts_after_one_declines(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(emit=())
        hold = Hold()
        script_subagents_then_reply(
            room, hold, (AGENT_CALL, SUBAGENT_TASK), (OTHER_CALL, OTHER_TASK)
        )

        async with asyncio.TaskGroup() as group:
            group.create_task(room.send("Research this"))
            async with hold:
                cli = room.claude.sessions[-1]
                await cli.background_tool(
                    SUBAGENT_TASK, "sub-decline", SUBAGENT_DECLINE
                )
                await cli.background_tool(OTHER_TASK, "other-reply", SUBAGENT_REPLY)

        assert room.chat == [SUBAGENT_REPLY.arguments["content"], MAIN_REPLY]
