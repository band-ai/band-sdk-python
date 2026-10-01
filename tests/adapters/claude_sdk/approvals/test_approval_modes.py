"""How each approval mode decides a native tool call, seen from the room."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import (
    APPROVAL_POLICY_DECISION_TEMPLATE,
    ClaudeSDKAdapterConfig,
)
from tests.adapters.claude_sdk.helpers import WRITE_NOTE, ClaudeRoom, with_approvals

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


def policy_notice(decision: str) -> str:
    return APPROVAL_POLICY_DECISION_TEMPLATE.format(
        summary="Write: notes.md", decision=decision
    )


@pytest.mark.parametrize(
    ("config", "notices", "write_output"),
    [
        pytest.param(
            with_approvals("auto_accept"),
            [policy_notice("accept")],
            "Write ran",
            id="auto-accept",
        ),
        pytest.param(
            with_approvals("auto_accept", text_notifications=False),
            [],
            "Write ran",
            id="auto-accept-quietly",
        ),
        pytest.param(
            with_approvals("auto_decline"),
            [policy_notice("decline")],
            "Tool use declined by policy: Write: notes.md",
            id="auto-decline",
        ),
        pytest.param(ClaudeSDKAdapterConfig(), [], "Write ran", id="approvals-off"),
    ],
)
async def test_each_mode_decides_a_file_write_without_waiting_on_anyone(
    claude_room: OpenRoom,
    config: ClaudeSDKAdapterConfig,
    notices: list[str],
    write_output: str,
) -> None:
    """With approvals on, even a write acceptEdits would auto-approve goes
    through the adapter's policy; with them off, acceptEdits runs it."""
    room = await claude_room(config)
    room.claude.script([WRITE_NOTE, room.model_reply("Handled.")])

    await room.send("Jot a note")

    assert room.chat == [*notices, "Handled."]
    assert room.tool_outputs["Write"] == write_output
    assert room.failures == []


async def test_room_commands_are_ordinary_prompts_when_approvals_are_off(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room()
    room.claude.script(
        [room.model_reply("Here is my status.")],
        [room.model_reply("Nothing to approve.")],
    )

    await room.send("/status")
    await room.send("/approve a-1")

    status_prompt, approve_prompt = room.claude.prompts
    assert "/status" in status_prompt
    assert "/approve a-1" in approve_prompt
    assert room.chat == ["Here is my status.", "Nothing to approve."]
