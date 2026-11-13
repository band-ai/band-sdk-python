"""A provider's Band call is refused only once its turn's decline has landed."""

from __future__ import annotations

import asyncio

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from band.integrations.mcp.backends import (
    BandMCPBackendSettings,
    backend_tool_registrations,
)
from band.integrations.mcp.engine import EngineSpec, build_engine
from band.runtime.custom_tools import declares_turn_effect, get_custom_tool_name
from band.runtime.tools import (
    CHAT_ID_FIELD_NAME,
    TOOL_DEFINITIONS,
    BandTool,
    TurnEffect,
)
from band.runtime.tools.effects import DECLINED_TURN_REFUSAL
from tests.framework_conformance.outcomeprobes import ANSWER, REPLY
from tests.framework_conformance.turnprobes import ROOM_ID, turn_tools


class HeldDeclineInput(BaseModel):
    """Decline to reply, once released."""


def refused(result: CallToolResult) -> bool:
    return result.isError and result.content == [
        TextContent(
            type="text",
            text=f"Error executing tool {BandTool.SEND_MESSAGE}: {DECLINED_TURN_REFUSAL}",
        )
    ]


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
        server = build_engine(
            EngineSpec(name="band", tools=tuple(backend_tool_registrations(settings)))
        )
        reply = {**REPLY.arguments, CHAT_ID_FIELD_NAME: ROOM_ID}

        async with (
            create_connected_server_and_client_session(server) as session,
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

        assert (before.isError, refused(after)) == (False, True)
        assert tools.chat == [ANSWER]
