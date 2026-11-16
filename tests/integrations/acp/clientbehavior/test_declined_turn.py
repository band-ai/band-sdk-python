"""A declined ACP turn refuses the agent's later Band calls and keeps its session."""

from __future__ import annotations

import json

from band.core.types import Emit
from band.integrations.acp.types import ToolResultRoomEvent
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from band.testing import FakeAgentTools
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)
from tests.mcp.toolresults import content_blocks, mcp_text_result, mcp_tool_error

ANSWER = {"content": "The vault code is 4471-ECHO.", "mentions": ["@pat"]}


class TestDeclinedTurn:
    async def test_a_refused_late_reply_leaves_the_session_ready_to_answer(
        self, fake_agent: FakeACPAgent
    ) -> None:
        fake_agent.will_call_mcp_tool(
            "tc-decline", BandTool.NO_REPLY, arguments={"reason": "FYI only"}
        ).will_call_mcp_tool("tc-late", BandTool.SEND_MESSAGE, arguments=ANSWER)
        config = fake_agent_config(inject_band_tools=True)
        async with acp_adapter(fake_agent, config, emit={Emit.TOOL_CALLS}) as session:
            declined = await session.send_judged("FYI: the deploy finished.")
            declined_session = session.session_id(session.room().room_id)

            @fake_agent.on_prompt
            async def answer(agent: FakeACPAgent, sid: str) -> None:
                await agent.call_mcp_tool(
                    session_id=sid,
                    server=BAND_MCP_SERVER_NAME,
                    tool_name=BandTool.SEND_MESSAGE,
                    arguments=ANSWER,
                )

            answered = await session.send_judged("What is the vault code?")

            assert session.session_id(session.room().room_id) == declined_session
        results = {
            result.tool_call_id: result for result in declined.parsed_tool_results
        }
        declined_result = await FakeAgentTools().no_reply()
        refusal = mcp_tool_error(BandTool.SEND_MESSAGE, DECLINED_TURN_REFUSAL)
        assert declined.texts == []
        assert results["tc-decline"] == ToolResultRoomEvent(
            name=BandTool.NO_REPLY,
            output=str(content_blocks(mcp_text_result(json.dumps(declined_result)))),
            tool_call_id="tc-decline",
            is_error=False,
        )
        assert results["tc-late"] == ToolResultRoomEvent(
            name=BandTool.SEND_MESSAGE,
            output=str(content_blocks(refusal)),
            tool_call_id="tc-late",
            is_error=True,
        )
        assert answered.texts == [ANSWER["content"]]
