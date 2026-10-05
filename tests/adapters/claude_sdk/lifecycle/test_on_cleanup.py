from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import looptime
import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


@pytest.mark.parametrize(
    "virtual_clock",
    [
        pytest.param(False, marks=pytest.mark.looptime(False), id="real-clock"),
        pytest.param(True, marks=pytest.mark.looptime, id="virtual-clock"),
    ],
)
async def test_fixture_cleanup_keeps_the_clock_that_started_the_server(
    monkeypatch: pytest.MonkeyPatch,
    claude_room: OpenRoom,
    virtual_clock: bool,
) -> None:
    room = await claude_room()
    room.claude.script([room.model_reply("done")])
    await room.send("hello")
    loop = asyncio.get_running_loop()
    assert isinstance(loop, looptime.LoopTimeEventLoop)
    cleanup_all = room.adapter.cleanup_all

    async def cleanup_on_original_clock() -> None:
        assert loop.looptime_on == virtual_clock
        await cleanup_all()

    # Keep the real server cleanup, observing its clock during fixture teardown.
    monkeypatch.setattr(room.adapter, "cleanup_all", cleanup_on_original_clock)


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


async def test_a_cancelled_message_never_leaks_its_turn_into_the_next(
    claude_room: OpenRoom,
) -> None:
    """The runtime cancelling a message cancels its detached turn and retires
    that CLI process, so nothing the abandoned turn would still do reaches the
    room or is read as the next message's answer; the next message resumes the
    conversation in a fresh process."""
    room = await claude_room()
    abandoned = Hold()
    room.claude.script(
        [room.model_reply("first")],
        [abandoned, room.model_reply("abandoned reply")],
        [room.model_reply("second")],
    )

    await room.send("quick")
    message = asyncio.create_task(room.send("slow"))
    async with abandoned:
        message.cancel()
        with pytest.raises(asyncio.CancelledError):
            await message
    await room.send("again")

    assert room.chat == ["first", "second"]
    assert room.failures == []
    assert room.claude.resumed == [None, "sess-1"]


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
