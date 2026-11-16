"""A served custom tool is refused, without running, once its turn declined."""

from __future__ import annotations

from pydantic import BaseModel

from band.integrations.mcp.backends import BandMCPBackendSettings
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import (
    CHAT_ID_FIELD_NAME,
    TOOL_DEFINITIONS,
    BandTool,
    TurnEffect,
)
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.framework_conformance.outcomeprobes import DECLINE
from tests.framework_conformance.turnprobes import ROOM_ID, turn_tools
from tests.mcp.toolresults import mcp_tool_error
from tests.mcpclient import in_memory_backend_session


class DeployInput(BaseModel):
    """Deploy the service."""


class TestDeclinedCustomTool:
    async def test_a_custom_act_after_a_decline_is_refused_and_never_runs(
        self,
    ) -> None:
        deployed: list[DeployInput] = []

        @declares_turn_effect(TurnEffect.ACT)
        async def deploy(args: DeployInput) -> str:
            deployed.append(args)
            return "deployed"

        tools = turn_tools()
        settings = BandMCPBackendSettings(
            tool_definitions=[TOOL_DEFINITIONS[BandTool.NO_REPLY]],
            get_tools=lambda _room_id: tools,
            additional_tools=[(DeployInput, deploy)],
        )
        name = get_custom_tool_name(DeployInput)

        async with in_memory_backend_session(settings) as session:
            await session.call_tool(
                DECLINE.name, {**DECLINE.arguments, CHAT_ID_FIELD_NAME: ROOM_ID}
            )
            late = await session.call_tool(name, {CHAT_ID_FIELD_NAME: ROOM_ID})

        assert late == mcp_tool_error(name, DECLINED_TURN_REFUSAL)
        assert deployed == []
