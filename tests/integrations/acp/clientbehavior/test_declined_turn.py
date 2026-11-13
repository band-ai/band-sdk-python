"""A declined ACP turn refuses the agent's later Band calls and keeps its session."""

from __future__ import annotations

from band.core.types import Emit
from band.integrations.acp.types import ToolResultRoomEvent
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    acp_adapter,
    fake_agent_config,
)

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
            result.tool_call_id: result
            for result in map(
                ToolResultRoomEvent.model_validate_json,
                (event["content"] for event in declined.tool_results),
            )
        }
        assert declined.texts == []
        assert {call: result.is_error for call, result in results.items()} == {
            "tc-decline": False,
            "tc-late": True,
        }
        assert DECLINED_TURN_REFUSAL in results["tc-late"].output
        assert answered.texts == [ANSWER["content"]]
