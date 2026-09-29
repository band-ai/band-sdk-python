"""Options a host embedding the Claude CLI sets on the adapter reach the CLI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def _cli_options(room: ClaudeRoom):
    """The options the room's CLI process was started with."""
    room.claude.script([room.model_reply("Hello.")])
    await room.send("hi")
    [session] = room.claude.sessions
    return session.options


@pytest.mark.parametrize("mode", ["auto", "dontAsk"])
async def test_headless_permission_modes_reach_the_cli(
    claude_room: OpenRoom, mode: str
) -> None:
    options = await _cli_options(await claude_room(permission_mode=mode))
    assert options.permission_mode == mode
