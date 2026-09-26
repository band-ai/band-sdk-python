from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from claude_agent_sdk import ClaudeSDKError

from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.fakecli import Raw
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_stream_the_sdk_cannot_parse_fails_the_turn_visibly(
    claude_room: OpenRoom,
) -> None:
    """A CLI message the SDK can't parse (e.g. version skew) aborts the turn
    with a generic room-visible failure instead of a silent one."""
    room = await claude_room()
    room.claude.script(
        [Raw({"type": "assistant", "message": {"model": "claude-fake"}})]
    )

    with pytest.raises(ClaudeSDKError, match="Missing required field"):
        await room.send("hello")

    assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
