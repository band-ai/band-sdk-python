"""A Band MCP server that dies between turns is replaced on a new port, and
every room's next session dials the live one."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.integrations.claude_sdk.session_manager import (
    ClaudeSessionManagerStoppedError,
)
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


async def test_a_message_after_shutdown_is_refused(claude_room: OpenRoom) -> None:
    """Shutdown closes the backend for good: a late message can't start a
    server nothing would stop."""
    room = await claude_room()
    backend = room.adapter._mcp.current
    assert backend is not None
    await room.adapter.cleanup_all()

    with pytest.raises(RuntimeError, match="stopped"):
        await room.send("hi")

    assert backend.is_running is False
    assert room.claude.sessions == []


async def test_a_message_caught_by_shutdown_restarts_nothing(
    claude_room: OpenRoom,
) -> None:
    """A message whose session request lands after the session manager
    stopped is refused, not retried as a failed resume that would bring the
    stopped manager back to life and report a failure mid-shutdown."""
    room = await claude_room()
    manager = room.adapter._session_manager
    assert manager is not None
    await manager.stop()

    with pytest.raises(ClaudeSessionManagerStoppedError):
        await room.send("hi", session_id="sess-earlier")

    assert room.reported_failures == []
    assert room.claude.sessions == []


async def test_a_restarted_agent_serves_band_tools_again(claude_room: OpenRoom) -> None:
    room = await claude_room()
    room.claude.script([room.model_reply("back again")])
    await room.adapter.cleanup_all()

    await room.adapter.on_started("Test Agent", "An agent under test")
    await room.send("hi")

    assert room.chat == ["back again"]
