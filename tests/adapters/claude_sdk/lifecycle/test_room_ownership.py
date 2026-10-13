"""Each room's Claude session is owned through its residency: accepted turns
keep the room leased until they settle, retired CLIs lose authority, and a
replacement CLI continues the same private conversation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE,
    RETIRED_SESSION_DENIAL,
    ClaudeSDKAdapterConfig,
)
from band.core.exceptions import CleanupIncompleteError
from tests.adapters.claude_sdk.fakecli import Hold, Recall, Remember
from tests.adapters.claude_sdk.helpers import (
    REPORTING_BUDGET,
    WRITE_NOTE,
    ClaudeRoom,
    with_approvals,
)

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

WRITE_PROMPT = APPROVAL_REQUESTED_TEMPLATE.format(
    summary="Write: notes.md", token="a-1"
)


async def test_a_parked_approval_keeps_its_turn_owned_until_the_room_leaves(
    claude_room: OpenRoom,
) -> None:
    """``on_message`` returns while the turn waits on a person; the detached
    turn still holds the room, and leaving settles it before the CLI closes."""
    room = await claude_room(with_approvals())
    room.claude.script([WRITE_NOTE, room.model_reply("never sent")])

    await room.send("jot a note")
    await room.until_said("Approval requested")
    parked = room.turn_running
    closing = room.claude.closing = Hold()
    leaving = asyncio.create_task(room.leave())
    async with closing:
        running_at_close = room.turn_running
    await leaving

    assert (parked, running_at_close) == (True, False)
    assert [cli.alive for cli in room.claude.sessions] == [False]
    assert room.chat == [WRITE_PROMPT]
    assert room.failures == []


async def test_a_permission_request_from_a_left_rooms_cli_is_denied(
    claude_room: OpenRoom,
) -> None:
    """A rejoined room starts counting residencies again, so only the session
    object tells the old CLI's late request apart; it never reaches the room."""
    room = await claude_room(with_approvals())
    room.claude.script([room.model_reply("first")], [room.model_reply("second")])
    await room.send("hi")
    await room.leave()
    await room.send("back again")
    old_cli, _ = room.claude.sessions
    assert old_cli.options.can_use_tool is not None

    decision = await old_cli.options.can_use_tool(
        "Bash", {"command": "ls"}, ToolPermissionContext(tool_use_id="late")
    )

    assert decision == PermissionResultDeny(message=RETIRED_SESSION_DENIAL)
    assert room.chat == ["first", "second"]


async def band_server_replaced(room: ClaudeRoom) -> None:
    await room.crash_band_server()


async def turn_cancelled(room: ClaudeRoom) -> None:
    abandoned = Hold()
    room.claude.script([abandoned, room.model_reply("never sent")])
    message = room.send_in_background("long job")
    async with abandoned:
        message.cancel()
        with pytest.raises(asyncio.CancelledError):
            await message


@pytest.mark.parametrize("retire", [band_server_replaced, turn_cancelled])
async def test_a_replacement_cli_recalls_what_the_retired_one_kept_private(
    claude_room: OpenRoom, retire: Callable[[ClaudeRoom], Awaitable[None]]
) -> None:
    """The note never reached the room, so only a resumed conversation can
    answer with it, in the workspace the retired CLI used."""
    room = await claude_room()
    room.claude.script([Remember("the code word is tulip"), room.model_reply("noted")])
    await room.send("remember the code word")

    await retire(room)
    room.claude.script([Recall(room.model_reply)])
    await room.send("what was the code word?")

    first, *_, last = room.claude.sessions
    assert room.chat == ["noted", "the code word is tulip"]
    assert (first.alive, last.alive) == (False, True)
    assert last.options.resume == first.session_id
    assert last.options.cwd == first.options.cwd
    assert room.failures == []


async def test_shutdown_keeps_mcp_for_open_rooms(claude_room: OpenRoom) -> None:
    """A CLI that is slow to close makes shutdown report incomplete cleanup;
    the Band MCP server it dials stays up until a retry confirms the room
    closed, and nothing is closed twice."""
    room = await claude_room(ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET))
    room.claude.script([room.model_reply("hi")])
    await room.send("hi")
    backend = room.adapter._mcp.current
    assert backend is not None
    closing = room.claude.closing = Hold()

    with pytest.raises(CleanupIncompleteError):
        await room.adapter.cleanup_all()
    [cli] = room.claude.sessions
    still_serving = (room.adapter._mcp.current is backend, cli.close_calls)
    closing.released.set()
    await room.adapter.cleanup_all()

    assert still_serving == (True, 1)
    assert (backend.is_running, cli.alive) == (False, False)


async def test_shutdown_steps_share_one_budget(
    claude_room: OpenRoom, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing the room and then the Band MCP server each fit the budget on
    their own but not together, so shutdown reports incomplete cleanup."""
    room = await claude_room(ClaudeSDKAdapterConfig(retention=REPORTING_BUDGET))
    room.claude.script([room.model_reply("hi")])
    await room.send("hi")
    step_s = REPORTING_BUDGET.transition_timeout_s * 0.7
    closing = room.claude.closing = Hold()
    mcp = room.adapter._mcp
    close_mcp = mcp.close

    # The server's real shutdown is quick; only its delay is staged here.
    async def slow_mcp_close(*, final: bool) -> None:
        await asyncio.sleep(step_s)
        await close_mcp(final=final)

    monkeypatch.setattr(mcp, "close", slow_mcp_close)
    shutdown = asyncio.create_task(room.adapter.cleanup_all())
    async with closing:
        await asyncio.sleep(step_s)

    with pytest.raises(CleanupIncompleteError):
        await shutdown
    [cli] = room.claude.sessions
    room_closed_first = not cli.alive
    monkeypatch.undo()
    await room.adapter.cleanup_all()

    assert room_closed_first
