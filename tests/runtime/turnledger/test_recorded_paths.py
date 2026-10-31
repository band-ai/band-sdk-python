"""Tool entry points record replies and completed work on the turn."""

from __future__ import annotations

from band.core.delivery import deliver_reply
from band.runtime.tools import AgentTools, BandTool


class TestRecordedCallPaths:
    async def test_a_direct_send_message_is_the_reply(self, tools: AgentTools) -> None:
        await tools.send_message("hi", mentions=["@alice"])

        assert tools.turn.replied
        assert tools.turn.complete

    async def test_a_dispatched_band_no_reply_declines(self, tools: AgentTools) -> None:
        await tools.execute_tool_call(BandTool.NO_REPLY, {"reason": "FYI only"})

        assert tools.turn.replied
        assert tools.turn.complete

    async def test_deliver_reply_is_the_reply(self, tools: AgentTools) -> None:
        await deliver_reply(tools, "the answer", ["@alice"])

        assert tools.turn.replied

    async def test_real_work_completes_without_a_reply(self, tools: AgentTools) -> None:
        await tools.create_chatroom()

        assert tools.turn.complete
        assert not tools.turn.replied
