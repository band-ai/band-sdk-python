"""Each room's Claude session dials its own room-bound Band MCP endpoint, so
the model never supplies the room."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from band.runtime.tools import BandTool
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.mcpclient import advertised_arguments, endpoint_path, mcp_session

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_rooms_dial_their_own_band_endpoint_on_one_server(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room()
    other_room = room.beside("room-2")
    room.claude.script(
        [room.model_reply("for room one")], [other_room.model_reply("for room two")]
    )

    await room.send("hi")
    await other_room.send("hi")

    endpoints = room.session_band_urls
    async with mcp_session(endpoints[0]) as session:
        advertised = await advertised_arguments(session, BandTool.SEND_MESSAGE)
    urls = [urlsplit(endpoint) for endpoint in endpoints]

    assert len({url.netloc for url in urls}) == 1
    assert [url.path for url in urls] == [
        endpoint_path(room_id="room-1"),
        endpoint_path(room_id="room-2"),
    ]
    assert "chat_id" not in advertised
    assert room.chat == ["for room one"]
    assert other_room.chat == ["for room two"]
