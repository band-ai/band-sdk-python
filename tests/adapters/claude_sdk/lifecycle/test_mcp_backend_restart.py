"""A Band MCP server that dies between turns is restarted on a new port, and
every room's next session dials the live one."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_new_room_after_a_crash_gets_a_live_band_endpoint(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room()
    other_room = room.beside("room-2")
    room.claude.script(
        [room.model_reply("before the crash")],
        [other_room.model_reply("after the crash")],
    )
    await room.send("hi")

    await room.crash_band_server()
    await other_room.send("hi")

    first_port, second_port = room.session_band_ports
    assert other_room.chat == ["after the crash"]
    assert second_port != first_port


async def test_an_open_room_after_a_crash_resumes_on_the_new_endpoint(
    claude_room: OpenRoom,
) -> None:
    """The room's open session still dials the dead port, so it is replaced by
    one that resumes the same conversation on the live one."""
    room = await claude_room()
    room.claude.script(
        [room.model_reply("before the crash")],
        [room.model_reply("after the crash")],
    )
    await room.send("hi")

    await room.crash_band_server()
    await room.send("still there?")

    first_session, _ = room.claude.sessions
    first_port, second_port = room.session_band_ports
    assert room.chat == ["before the crash", "after the crash"]
    assert room.claude.resumed == [None, "sess-1"]
    assert first_session.alive is False
    assert second_port != first_port


async def test_a_message_parked_behind_shutdown_never_restarts_the_server(
    claude_room: OpenRoom,
) -> None:
    """A message waiting on the backend lock while cleanup_all stops the
    server must fail, not start a server nothing would ever stop.

    No message is sent first: an unstarted session manager lets cleanup_all
    reach the lock without suspending, so the ensure call genuinely parks
    behind it while the server's stop awaits its serve task.
    """
    room = await claude_room()
    adapter = room.adapter
    backend = adapter._mcp_backend
    assert backend is not None

    shutdown, parked = await asyncio.gather(
        adapter.cleanup_all(),
        adapter._ensure_mcp_backend(),
        return_exceptions=True,
    )

    assert shutdown is None
    assert isinstance(parked, RuntimeError)
    assert adapter._mcp_backend is None
    assert backend.is_running is False
