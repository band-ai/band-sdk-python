"""A Claude CLI process that dies is replaced, and the room hears about any
turn it took down with it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from claude_agent_sdk import CLIConnectionError

from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.fakecli import Hangup
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.baseline.decisions import ModelDecision

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_cli_that_dies_mid_turn_fails_only_an_unanswered_turn(
    claude_room: OpenRoom,
) -> None:
    """Dying after the reply went out completes the turn (failing it would
    make the runtime redeliver and answer twice); dying before any reply
    fails it. Either way the next message gets a fresh CLI process."""
    room = await claude_room()
    room.claude.script(
        [room.model_reply("Answered before the crash."), Hangup()],
        [
            ModelDecision.call("mcp__band__band_get_participants", chat_id="room-1"),
            Hangup(),
        ],
        [room.model_reply("Back on a fresh process.")],
    )

    await room.send("first")
    with pytest.raises(CLIConnectionError, match="ended without a result"):
        await room.send("second")
    await room.send("third")

    assert room.chat == ["Answered before the crash.", "Back on a fresh process."]
    assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
    assert len(room.claude.sessions) == 3


async def test_a_cli_that_died_while_idle_is_replaced_on_the_next_message(
    claude_room: OpenRoom,
) -> None:
    """The process crashed between turns: the message that finds it dead
    fails with a room-visible error, and the one after runs on a new one."""
    room = await claude_room()
    room.claude.script(
        [room.model_reply("Hello.")],
        [room.model_reply("Hello again.")],
    )

    await room.send("hi")
    room.claude.sessions[-1].die()
    with pytest.raises(CLIConnectionError):
        await room.send("are you there?")
    await room.send("hi again")

    assert room.chat == ["Hello.", "Hello again."]
    assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
    assert len(room.claude.sessions) == 2
