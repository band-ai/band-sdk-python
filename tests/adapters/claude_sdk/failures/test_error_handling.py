from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from claude_agent_sdk import ClaudeSDKError

from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.fakecli import Raw
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_turn_the_sdk_cannot_parse_never_leaks_into_the_next(
    claude_room: OpenRoom,
) -> None:
    """A CLI message the SDK can't parse (e.g. version skew) aborts the turn
    with a generic room-visible failure. The rest of that turn, which the CLI
    still emits, is never read as the next message's answer: the next message
    resumes the conversation in a fresh process."""
    room = await claude_room()
    room.claude.script(
        [room.model_reply("Hi.")],
        [Raw({"type": "assistant", "message": {"model": "claude-fake"}})],
        [room.model_reply("Recovered.")],
    )

    await room.send("hello")
    with pytest.raises(ClaudeSDKError, match="Missing required field"):
        await room.send("and now?")
    await room.send("still there?")

    assert room.chat == ["Hi.", "Recovered."]
    assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
    assert room.claude.resumed == [None, "sess-1"]
