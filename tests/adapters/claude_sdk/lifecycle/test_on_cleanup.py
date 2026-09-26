from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def leave_room(room: ClaudeRoom) -> None:
    await room.leave()


async def shut_down(room: ClaudeRoom) -> None:
    await room.adapter.cleanup_all()


@pytest.mark.parametrize("teardown", [leave_room, shut_down])
async def test_teardown_mid_turn_stops_the_turn_before_closing_its_session(
    claude_room: OpenRoom, teardown: Callable[[ClaudeRoom], Awaitable[None]]
) -> None:
    """Closing the session under a live turn would surface as a dead CLI; the
    turn is cancelled first, so the room sees neither a reply nor a failure."""
    room = await claude_room()
    thinking = Hold()
    room.claude.script([thinking, room.model_reply("never sent")])

    message = asyncio.create_task(room.send("long job"))
    async with thinking:
        await teardown(room)

    with pytest.raises(asyncio.CancelledError):
        await message
    [session] = room.claude.sessions
    assert not session.alive
    assert room.chat == []
    assert room.failures == []


async def test_a_room_left_mid_turn_can_be_rejoined_with_a_fresh_session(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room()
    thinking = Hold()
    room.claude.script(
        [thinking, room.model_reply("never sent")], [room.model_reply("welcome back")]
    )
    message = asyncio.create_task(room.send("long job"))
    async with thinking:
        await room.leave()
    with pytest.raises(asyncio.CancelledError):
        await message

    await room.send("I'm back", history="[Bob]: long job")

    assert len(room.claude.sessions) == 2
    assert "[Bob]: long job" in room.claude.prompts[-1]
    assert room.chat == ["welcome back"]


async def test_a_cancelled_message_cancels_its_turn_and_frees_the_room(
    claude_room: OpenRoom,
) -> None:
    """The runtime cancelling a message cancels its detached turn, so the
    room's next message is answered instead of refused."""
    room = await claude_room()
    abandoned = Hold()
    room.claude.script([abandoned], [room.model_reply("second")])

    message = asyncio.create_task(room.send("slow"))
    async with abandoned:
        message.cancel()
        with pytest.raises(asyncio.CancelledError):
            await message
        await room.send("again")

        assert room.chat == ["second"]
        assert room.failures == []


async def test_turn_task_exceptions_are_retrieved_without_raising() -> None:
    """A turn that fails after its message returned was already reported;
    retrieving its outcome must never raise, even for a cancelled turn."""

    async def failing_turn() -> None:
        raise RuntimeError("boom")

    failed = asyncio.create_task(failing_turn())
    cancelled = asyncio.create_task(asyncio.Event().wait())
    cancelled.cancel()
    await asyncio.wait([failed, cancelled])

    for turn in (failed, cancelled):
        ClaudeSDKAdapter()._log_turn_task_exception(turn)
