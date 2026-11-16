"""A served custom tool is refused, without running, once its turn declined."""

from __future__ import annotations

from band.integrations.mcp.backends import BandMCPBackendSettings
from band.runtime.tools import CHAT_ID_FIELD_NAME, TOOL_DEFINITIONS, BandTool
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.framework_conformance.outcomeprobes import DECLINE
from tests.framework_conformance.turnprobes import ROOM_ID, turn_tools
from tests.mcp.toolresults import mcp_tool_error
from tests.mcpclient import in_memory_backend_session
from tests.runtime.customtools.kit import DEPLOY, recording_deploy


class TestDeclinedCustomTool:
    async def test_a_custom_act_after_a_decline_is_refused_and_never_runs(
        self,
    ) -> None:
        deploy, deployed = recording_deploy()
        tools = turn_tools()
        settings = BandMCPBackendSettings(
            tool_definitions=[TOOL_DEFINITIONS[BandTool.NO_REPLY]],
            get_tools=lambda _room_id: tools,
            additional_tools=[deploy],
        )

        async with in_memory_backend_session(settings) as session:
            await session.call_tool(
                DECLINE.name, {**DECLINE.arguments, CHAT_ID_FIELD_NAME: ROOM_ID}
            )
            late = await session.call_tool(DEPLOY, {CHAT_ID_FIELD_NAME: ROOM_ID})

        assert late == mcp_tool_error(DEPLOY, DECLINED_TURN_REFUSAL)
        assert deployed == []
