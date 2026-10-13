"""turn_timeout_s bounds a Claude turn like the Codex and ACP adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig, TurnResultAlreadyReported
from band.client.streaming import ControlMode
from band.core.exceptions import CleanupIncompleteError, InputNotAccepted
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.fakecli import Hold, StreamFails
from tests.adapters.claude_sdk.helpers import REPORTING_BUDGET, ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

# Short enough to abandon the held turn quickly, long enough for the next
# turn's real loopback round trips to the Band MCP server on a slow runner.
TURN_TIMEOUT_S = 2.0


async def test_a_stuck_turn_is_interrupted_and_reported_as_a_timeout(
    claude_room: OpenRoom,
) -> None:
    """The interrupted turn's own result is drained, so the next turn on the
    same CLI process reads its own answer."""
    room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=TURN_TIMEOUT_S))
    room.claude.script([Hold()], [room.model_reply("Back again.")])

    with pytest.raises(TurnResultAlreadyReported):
        await room.send("take forever")
    await room.send("and now?")

    assert [f["code"] for f in room.reported_failures] == ["timeout"]
    assert room.failures == [f"Claude turn timed out after {TURN_TIMEOUT_S}s"]
    assert room.chat == ["Back again."]
    assert len(room.claude.sessions) == 1


async def test_a_turn_that_ignores_the_interrupt_is_closed_and_resumed(
    claude_room: OpenRoom, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("band.adapters.claude_sdk._TIMEOUT_DRAIN_SECONDS", 0.05)
    room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=TURN_TIMEOUT_S))
    room.claude.ignore_interrupt = True
    room.claude.script([Hold()], [room.model_reply("Fresh start.")])

    with pytest.raises(TurnResultAlreadyReported):
        await room.send("take forever")
    await room.send("and now?")

    assert [f["code"] for f in room.reported_failures] == ["timeout"]
    assert room.chat == ["Fresh start."]
    assert [session.alive for session in room.claude.sessions] == [False, True]


async def test_stop_during_timeout_recovery_ends_the_turn_without_a_timeout(
    claude_room: OpenRoom,
) -> None:
    """STOP takes over the wedged recovery instead of waiting out its drain
    bound, reports no timeout, and leaves no interrupted client behind."""
    room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=TURN_TIMEOUT_S))
    room.claude.ignore_interrupt = True
    room.claude.script([Hold()], [room.model_reply("Fresh start.")])
    sending = room.send_in_background("take forever")

    await room.claude.interrupted.wait()
    await room.adapter.on_interrupt(room.room_id, ControlMode.STOP)
    with pytest.raises(asyncio.CancelledError):
        await sending
    await room.send("and now?")

    assert room.reported_failures == []
    assert room.chat == ["Fresh start."]
    assert [session.alive for session in room.claude.sessions] == [False, True]


async def test_a_timeout_from_the_cli_stream_is_an_ordinary_failure(
    claude_room: OpenRoom,
) -> None:
    """Only the turn deadline is reported as a timeout; any other
    ``TimeoutError`` fails the turn and replaces the CLI process."""
    room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=30))
    room.claude.script(
        [StreamFails(TimeoutError("read timed out"))],
        [room.model_reply("Back on a fresh process.")],
    )

    with pytest.raises(TimeoutError):
        await room.send("first")
    await room.send("second")

    assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
    assert room.chat == ["Back on a fresh process."]
    assert len(room.claude.sessions) == 2


async def test_stop_retains_worker_until_session_cleanup_finishes(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room(
        ClaudeSDKAdapterConfig(
            turn_timeout_s=TURN_TIMEOUT_S, retention=REPORTING_BUDGET
        )
    )
    work, closing = Hold(), Hold()
    room.claude.script([work])
    room.claude.closing = closing
    sending = asyncio.create_task(room.send("work"))
    try:
        async with work:
            sending.cancel()
            await closing.reached.wait()
            await asyncio.gather(
                room.adapter.on_interrupt(room.room_id, ControlMode.STOP),
                room.adapter.on_interrupt(room.room_id, ControlMode.STOP),
            )
            with pytest.raises(CleanupIncompleteError):
                await room.leave()
            with pytest.raises(InputNotAccepted):
                await room.send("overlap")
            assert room.claude.sessions[0].alive
            closing.released.set()
        with pytest.raises(asyncio.CancelledError):
            await sending
        await room.settled()
        await room.leave()
        assert not room.claude.sessions[0].alive
        assert room.reported_failures == []
    finally:
        closing.released.set()
        work.released.set()
        await asyncio.gather(sending, return_exceptions=True)


async def test_a_cli_that_exits_when_interrupted_is_replaced(
    claude_room: OpenRoom,
) -> None:
    """The interrupted CLI exits without the turn's result; draining that end
    retires it as a crash, so the next message starts a new conversation
    instead of writing to a dead process."""
    room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=TURN_TIMEOUT_S))
    room.claude.exit_on_interrupt = True
    room.claude.script(
        [room.model_reply("Hello.")], [Hold()], [room.model_reply("Fresh start.")]
    )

    await room.send("hi")
    with pytest.raises(TurnResultAlreadyReported):
        await room.send("take forever")
    await room.send("and now?")

    assert room.failures == [f"Claude turn timed out after {TURN_TIMEOUT_S}s"]
    assert room.chat == ["Hello.", "Fresh start."]
    assert room.claude.resumed == [None, None]
