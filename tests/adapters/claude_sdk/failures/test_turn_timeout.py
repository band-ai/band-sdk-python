"""turn_timeout_s bounds a Claude turn like the Codex and ACP adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig, TurnResultAlreadyReported
from tests.adapters.claude_sdk.fakecli import Hold
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


def test_a_non_positive_timeout_is_refused() -> None:
    with pytest.raises(ValueError, match="turn_timeout_s"):
        ClaudeSDKAdapterConfig(turn_timeout_s=0)
