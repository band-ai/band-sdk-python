from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.adapters.claude_sdk import (
    DEFAULT_MODEL,
    TOOL_SEARCH,
    ClaudeSDKCommand,
)
from band.runtime.tools import MAX_INLINE_IMAGE_BYTES
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.baseline.decisions import ModelDecision

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

# claude_agent_sdk's stdio transport default (subprocess_cli._DEFAULT_MAX_BUFFER_SIZE).
SDK_DEFAULT_BUFFER_BYTES = 1024 * 1024


@pytest.mark.parametrize(
    ("config", "model", "fallback_model", "effort"),
    [
        ({}, DEFAULT_MODEL, None, None),
        (
            {"model": "opus", "fallback_model": "sonnet", "effort": "xhigh"},
            "opus",
            "sonnet",
            "xhigh",
        ),
    ],
    ids=["defaults", "configured"],
)
async def test_the_cli_starts_with_the_configured_model_and_a_large_buffer(
    claude_room: OpenRoom,
    config: dict[str, Any],
    model: str,
    fallback_model: str | None,
    effort: str | None,
) -> None:
    """An unpinned model falls back to DEFAULT_MODEL (the CLI's own pick fails
    under API-key auth). The line buffer must fit an inlined room-file image,
    which crashed the whole CLI connection at the SDK's default size."""
    room = await claude_room(**config)
    room.claude.script([room.model_reply("hi")])

    await room.send("hello")

    [session] = room.claude.sessions
    assert session.options.model == model
    assert session.options.fallback_model == fallback_model
    assert session.options.effort == effort
    assert session.options.max_buffer_size > SDK_DEFAULT_BUFFER_BYTES
    assert session.options.max_buffer_size > MAX_INLINE_IMAGE_BYTES * 4 // 3


async def test_manual_approval_gates_native_tools_but_never_band_tools_or_tool_search(
    claude_room: OpenRoom,
) -> None:
    """Band tools and ToolSearch (how the CLI loads deferred tool definitions)
    run straight through; only the native Bash call waits on the room."""
    room = await claude_room(approval_mode="manual", approval_wait_timeout_s=5)
    room.claude.script(
        [
            ModelDecision.call(TOOL_SEARCH, query="select:band_send_message"),
            ModelDecision.call("Bash", command="ls"),
            room.model_reply("listed"),
        ]
    )

    await room.send("list the files")
    [prompt] = room.chat
    assert prompt.startswith("Approval requested (Bash: `ls`)")

    await room.send(f"/{ClaudeSDKCommand.APPROVE}")
    await room.settled()

    assert room.chat[1:] == ["Approval `a-1` resolved as **accept**.", "listed"]
    assert set(room.tool_outputs) == {TOOL_SEARCH, "Bash", "band_send_message"}
