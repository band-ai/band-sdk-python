"""The model's own decline ends its native subagents' Band calls too."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.tools import BandTool
from tests.adapters.claude_sdk.activity.kit import (
    AGENT_CALL,
    REFUSED_SUBAGENT_REPLY,
    SUBAGENT_REPLY,
    SUBAGENT_TASK,
    native_task_started,
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.helpers import OpenRoom
from tests.adapters.hold import Hold


class TestModelDeclineEndsSubagents:
    @pytest.mark.parametrize(
        "decline_at",
        [0, 2],
        ids=["started-after-decline", "running-at-decline"],
    )
    async def test_the_subagents_later_call_is_refused(
        self, claude_room: OpenRoom, decline_at: int
    ) -> None:
        room = await claude_room(emit=())
        hold = Hold()
        start_subagent = [
            native_tool_uses((AGENT_CALL, "Agent")),
            native_task_started(SUBAGENT_TASK, AGENT_CALL),
        ]
        start_subagent.insert(
            decline_at, room.model_call(BandTool.NO_REPLY, reason="not for me")
        )
        room.claude.script([*start_subagent, hold, native_tool_result(AGENT_CALL)])

        async with asyncio.TaskGroup() as group:
            group.create_task(room.send("Hi"))
            async with hold:
                late = await room.claude.sessions[-1].background_tool_result(
                    SUBAGENT_TASK, "sub-late", SUBAGENT_REPLY
                )

        assert late == REFUSED_SUBAGENT_REPLY
        assert room.chat == []
