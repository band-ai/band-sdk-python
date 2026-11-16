"""OpenCode: once the model declines, its later Band call is refused over MCP."""

from __future__ import annotations

import json

from band.runtime.tools import BandTool
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    event_message_updated,
    event_session_idle,
    run_single_turn,
)
from tests.framework_conformance.outcomeprobes import DECLINE, DECLINE_ACK, REPLY
from tests.mcp.toolresults import mcp_text_result, mcp_tool_error


class TestDeclinedTurn:
    async def test_a_reply_after_the_decline_comes_back_as_the_refusal(
        self, make_adapter, tools: FakeAgentTools, mcp_backend: BandMCPCalls
    ) -> None:
        client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-1", "msg-1"),
                    mcp_backend.band_tool_attempt(DECLINE.name, DECLINE.arguments),
                    mcp_backend.band_tool_attempt(REPLY.name, REPLY.arguments),
                    event_session_idle("sess-1"),
                ]
            ]
        )

        await run_single_turn(make_adapter(client), tools)

        assert mcp_backend.results == [
            mcp_text_result(json.dumps(DECLINE_ACK)),
            mcp_tool_error(BandTool.SEND_MESSAGE, DECLINED_TURN_REFUSAL),
        ]
        assert tools.chat == []
