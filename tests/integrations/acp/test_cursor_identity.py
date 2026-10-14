"""Cursor MCP identity survives permissions and room narration."""

from __future__ import annotations

import pytest
from acp.helpers import update_agent_message_text
from acp.schema import ToolCallProgress, ToolCallStart
from pydantic import BaseModel, create_model

from band.adapters.cursor_acp import CursorACPAdapter
from band.integrations.acp.room_emitter import RoomTurnEmitter
from band.integrations.acp.types import ToolCallRoomEvent, ToolResultRoomEvent
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool, mcp_tool_spelling
from band.testing import FakeAgentTools, events_of_type


class AliasArguments(BaseModel):
    """An accepted custom name that overlaps the MCP reply spelling."""

    content: str


async def alias_reply(arguments: AliasArguments) -> str:
    return arguments.content


@pytest.mark.asyncio
@pytest.mark.parametrize("with_alias", [False, True], ids=["plain", "custom-alias"])
@pytest.mark.parametrize(
    ("title", "is_reply"),
    [
        ("band-band_send_message: band_send_message", True),
        ("other-band_send_message: band_send_message", False),
        ("band-band_send_message: band_no_reply", False),
    ],
    ids=["band-title", "foreign-server", "mismatched-halves"],
)
async def test_cursor_title_preserves_reply_identity_in_room_events(
    with_alias: bool,
    title: str,
    is_reply: bool,
) -> None:
    alias_model = create_model(
        f"{mcp_tool_spelling(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE)}Input",
        __base__=AliasArguments,
    )
    adapter = CursorACPAdapter(
        additional_tools=[(alias_model, alias_reply)] if with_alias else None,
    )
    client = adapter._runtime_client_factory()
    tools = FakeAgentTools()
    async with RoomTurnEmitter(
        tools, session_id="session", room_id="room", records_tool_effects=True
    ) as emitter:
        client.set_sink("session", emitter.emit)
        await client.session_update(
            "session",
            ToolCallStart(
                session_update="tool_call",
                tool_call_id="reply",
                title=title,
                status="in_progress",
                raw_input={"content": "Reply"},
            ),
        )
        await client.session_update(
            "session",
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id="reply",
                status="completed",
                raw_output={"id": "message"},
            ),
        )
        await client.session_update("session", update_agent_message_text("Posted it."))
        await client.flush("session")

    call = ToolCallRoomEvent.model_validate_json(
        events_of_type(tools, "tool_call")[0]["content"]
    )
    result = ToolResultRoomEvent.model_validate_json(
        events_of_type(tools, "tool_result")[0]["content"]
    )
    assert tools.turn.replied is is_reply
    assert call.name == result.name == (BandTool.SEND_MESSAGE if is_reply else title)
    assert [event["content"] for event in events_of_type(tools, "thought")] == (
        [] if is_reply else ["Posted it."]
    )
