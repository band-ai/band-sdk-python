"""A model repeating a finished activity call still ends its turn normally."""

from __future__ import annotations

import json

from band.adapters.pydantic_ai import PydanticAIAdapter
from band.core.types import Emit, ToolEventKey
from band.runtime.tools import BandTool
from band.runtime.tools.schema import tool_execution_failure
from band.testing import FakeAgentTools, events_of_type, failure_reports
from tests.adapters.pydanticai.kit import _reply_arguments, _streamed_tool_calls
from tests.e2e.baseline.toolkit.activitywork import (
    LOOKUP_PATH,
    ActivityWork,
    Operation,
    repeated_invocation,
)
from tests.framework_conformance.activitycapture.lookup import HeldLookupPeer
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, user_message

REPORTED = "C is done"


class TestActivityRepeat:
    async def test_a_repeat_after_the_reply_is_a_failed_result_not_a_failed_turn(
        self, activity_work: tuple[ActivityWork, HeldLookupPeer]
    ) -> None:
        work, peer = activity_work
        work[Operation.C].release.set()
        lookup = {"key": Operation.C.value, "private_note": work.note}
        message = user_message(work.instruction(Operation.C))
        adapter = PydanticAIAdapter(
            llm=_streamed_tool_calls(
                (work.tool.name, lookup),
                (BandTool.SEND_MESSAGE, _reply_arguments(message, REPORTED)),
                (work.tool.name, lookup),
            ),
            additional_tools=[work.tool.as_custom_tool_def()],
            emit=Emit.TOOL_CALLS,
        )
        await adapter.on_started("Agent", "An agent")
        tools = FakeAgentTools(room_id=ROOM_ID)

        await adapter.on_event(turn_input(tools, message))

        results = [
            json.loads(event["content"])
            for event in events_of_type(tools, "tool_result")
        ]
        assert [
            (result[ToolEventKey.NAME], result[ToolEventKey.IS_ERROR])
            for result in results
        ] == [
            (work.tool.name, False),
            (BandTool.SEND_MESSAGE, False),
            (work.tool.name, True),
        ]
        assert results[0][ToolEventKey.OUTPUT] == await work.completed(Operation.C)
        assert results[2][ToolEventKey.OUTPUT] == tool_execution_failure(
            work.tool.name, repeated_invocation(Operation.C)
        )
        assert peer.requests == [LOOKUP_PATH]
        assert [sent["content"] for sent in tools.messages_sent] == [REPORTED]
        assert failure_reports(tools) == []
