"""turn_timeout_s bounds a Claude turn like the Codex and ACP adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig, TurnResultAlreadyReported
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.fakecli import Hold, StreamFails
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_stuck_turn_is_interrupted_and_reported_as_a_timeout(
    claude_room: OpenRoom,
) -> None:
    """The interrupted turn's own result is drained, so the next turn on the
    same CLI process reads its own answer."""
    room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=0.05))
    room.claude.script([Hold()], [room.model_reply("Back again.")])

    with pytest.raises(TurnResultAlreadyReported):
        await room.send("take forever")
    await room.send("and now?")

    assert [f["code"] for f in room.reported_failures] == ["timeout"]
    assert room.failures == ["Claude turn timed out after 0.05s"]
    assert room.chat == ["Back again."]
    assert len(room.claude.sessions) == 1


async def test_a_turn_that_ignores_the_interrupt_is_closed_and_resumed(
    claude_room: OpenRoom, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("band.adapters.claude_sdk._TIMEOUT_DRAIN_SECONDS", 0.05)
    room = await claude_room(ClaudeSDKAdapterConfig(turn_timeout_s=0.05))
    room.claude.ignore_interrupt = True
    room.claude.script([Hold()], [room.model_reply("Fresh start.")])

    with pytest.raises(TurnResultAlreadyReported):
        await room.send("take forever")
    await room.send("and now?")

    assert [f["code"] for f in room.reported_failures] == ["timeout"]
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
