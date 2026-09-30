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


FALLBACK_WARNING = (
    "band.adapters.claude_sdk",
    logging.WARNING,
    (
        "Room room-1: Claude CLI runs permission mode default instead of the "
        "requested auto"
    ),
)


async def test_an_unavailable_mode_is_warned_about_once(
    claude_room: OpenRoom, caplog: pytest.LogCaptureFixture
) -> None:
    room = await claude_room(permission_mode="auto")
    room.claude.unavailable_modes.add("auto")
    room.claude.script([room.model_reply("Hello.")], [room.model_reply("Again.")])

    await room.send("hi")
    await room.send("hi again")

    assert caplog.record_tuples.count(FALLBACK_WARNING) == 1


@pytest.mark.parametrize("approval_mode", ["manual", "auto_accept", "auto_decline"])
def test_dont_ask_refuses_every_approval_mode_it_would_bypass(
    approval_mode: str,
) -> None:
    with pytest.raises(ValueError, match=DONT_ASK_PERMISSION_MODE):
        ClaudeSDKAdapter(
            permission_mode=DONT_ASK_PERMISSION_MODE, approval_mode=approval_mode
        )
