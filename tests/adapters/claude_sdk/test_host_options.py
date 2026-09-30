"""How the permission mode a host picks plays out in the Claude CLI session."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import DONT_ASK_PERMISSION_MODE, ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_dont_ask_denies_an_unlisted_native_tool_without_asking_the_room(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room(permission_mode=DONT_ASK_PERMISSION_MODE)
    room.claude.script([WRITE_NOTE, room.model_reply("Could not write.")])

    await room.send("Jot a note")

    assert room.tool_outputs["Write"] == "Permission denied"
    assert room.chat == ["Could not write."]
    assert room.failures == []


async def test_auto_reaches_the_cli(claude_room: OpenRoom) -> None:
    room = await claude_room(permission_mode="auto")
    room.claude.script([room.model_reply("Hello.")])

    await room.send("hi")

    [session] = room.claude.sessions
    assert session.options.permission_mode == "auto"


async def test_an_unavailable_mode_is_reported_as_the_one_in_force(
    claude_room: OpenRoom, caplog: pytest.LogCaptureFixture
) -> None:
    room = await claude_room(permission_mode="auto", approval_mode="manual")
    room.claude.unavailable_modes.add("auto")
    room.claude.script([room.model_reply("Hello.")])

    with caplog.at_level(logging.WARNING, logger="band.adapters.claude_sdk"):
        await room.send("hi")
        await room.send("/status")

    assert (
        "Claude CLI runs permission mode default instead of the requested auto"
        in caplog.messages
    )
    assert "- permission_mode: `default`" in room.chat[-1]


@pytest.mark.parametrize("approval_mode", ["manual", "auto_accept", "auto_decline"])
def test_dont_ask_refuses_every_approval_mode_it_would_bypass(
    approval_mode: str,
) -> None:
    with pytest.raises(ValueError, match=DONT_ASK_PERMISSION_MODE):
        ClaudeSDKAdapter(
            permission_mode=DONT_ASK_PERMISSION_MODE, approval_mode=approval_mode
        )
