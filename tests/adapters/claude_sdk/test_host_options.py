"""How the permission mode a host picks plays out in the Claude CLI session."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import (
    AUTO_FALLBACK_PERMISSION_MODE,
    ClaudeApprovalOptions,
    ClaudePermissionMode,
    ClaudeSDKAdapterConfig,
)
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_dont_ask_denies_an_unlisted_native_tool_without_asking_the_room(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room(
        ClaudeSDKAdapterConfig(permission_mode=ClaudePermissionMode.DONT_ASK)
    )
    room.claude.script([WRITE_NOTE, room.model_reply("Could not write.")])

    await room.send("Jot a note")

    assert room.tool_outputs["Write"] == "Permission denied"
    assert room.chat == ["Could not write."]
    assert room.failures == []


FALLBACK_WARNING = (
    "band.adapters.claude_sdk",
    logging.WARNING,
    (
        f"Room room-1: Claude CLI runs permission mode "
        f"{AUTO_FALLBACK_PERMISSION_MODE} instead of the requested "
        f"{ClaudePermissionMode.AUTO}"
    ),
)


async def test_an_unavailable_mode_is_warned_about_once(
    claude_room: OpenRoom, caplog: pytest.LogCaptureFixture
) -> None:
    room = await claude_room(
        ClaudeSDKAdapterConfig(permission_mode=ClaudePermissionMode.AUTO)
    )
    room.claude.unavailable_modes.add(ClaudePermissionMode.AUTO)
    room.claude.script([room.model_reply("Hello.")], [room.model_reply("Again.")])

    await room.send("hi")
    await room.send("hi again")

    assert caplog.record_tuples.count(FALLBACK_WARNING) == 1


@pytest.mark.parametrize("approval_mode", ["manual", "auto_accept", "auto_decline"])
def test_dont_ask_refuses_every_approval_mode_it_would_bypass(
    approval_mode: str,
) -> None:
    with pytest.raises(ValueError, match=ClaudePermissionMode.DONT_ASK):
        ClaudeSDKAdapterConfig(
            permission_mode=ClaudePermissionMode.DONT_ASK,
            approvals=ClaudeApprovalOptions(mode=approval_mode),
        )
