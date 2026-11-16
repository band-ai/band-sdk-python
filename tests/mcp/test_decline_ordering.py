"""A provider's Band call is refused only once its turn's decline has landed."""

from __future__ import annotations

import asyncio
import json

from band.integrations.mcp.backends import BandMCPBackendSettings
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import (
    CHAT_ID_FIELD_NAME,
    TOOL_DEFINITIONS,
    BandTool,
    TurnEffect,
)
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.framework_conformance.outcomeprobes import ANSWER, REPLY, REPLY_SENT
from tests.framework_conformance.turnprobes import ROOM_ID, turn_tools
from tests.mcp.toolresults import mcp_text_result, mcp_tool_error
from tests.mcpclient import in_memory_backend_session
from tests.runtime.customtools.kit import HeldDeclineInput


class TestDeclineOrdering:
    async def test_a_call_before_the_decline_lands_is_served_and_one_after_is_refused(
        self,
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        @declares_turn_effect(TurnEffect.DECLINE)
        async def held_decline() -> str:
            entered.set()
            await release.wait()
            return "declined"

        tools = turn_tools()
        settings = BandMCPBackendSettings(
            tool_definitions=[TOOL_DEFINITIONS[BandTool.SEND_MESSAGE]],
            get_tools=lambda _room_id: tools,
            additional_tools=[(HeldDeclineInput, held_decline)],
        )
        reply = {**REPLY.arguments, CHAT_ID_FIELD_NAME: ROOM_ID}

        async with (
            in_memory_backend_session(settings) as session,
            asyncio.TaskGroup() as group,
        ):
            decline = group.create_task(
                session.call_tool(
                    get_custom_tool_name(HeldDeclineInput),
                    {CHAT_ID_FIELD_NAME: ROOM_ID},
                )
            )
            await entered.wait()
            before = await session.call_tool(BandTool.SEND_MESSAGE, reply)
            release.set()
            await decline
            after = await session.call_tool(BandTool.SEND_MESSAGE, reply)

        assert before == mcp_text_result(json.dumps(REPLY_SENT))
        assert after == mcp_tool_error(BandTool.SEND_MESSAGE, DECLINED_TURN_REFUSAL)
        assert tools.chat == [ANSWER]
