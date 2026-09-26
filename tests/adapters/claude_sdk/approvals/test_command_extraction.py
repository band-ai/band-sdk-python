from __future__ import annotations

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKCommand


@pytest.mark.parametrize(
    ("content", "command"),
    [
        ("/approve a-1", (ClaudeSDKCommand.APPROVE, "a-1")),
        ("/Decline a-2", (ClaudeSDKCommand.DECLINE, "a-2")),
        ("/approvals", (ClaudeSDKCommand.APPROVALS, "")),
        ("/status", (ClaudeSDKCommand.STATUS, "")),
        ("/approve", (ClaudeSDKCommand.APPROVE, "")),
        ("  /approve a-1", (ClaudeSDKCommand.APPROVE, "a-1")),
        # A delivered reply arrives with the platform's @handle block first,
        # sometimes doubled by a human's inline mention.
        ("@alex/claude /approve a-1", (ClaudeSDKCommand.APPROVE, "a-1")),
        ("@alex/claude @alex/claude /decline a-2", (ClaudeSDKCommand.DECLINE, "a-2")),
        ("Hello, agent!", None),
        ("approve a-1", None),
        ("hey /approve a-1", None),
        ("///approve a-1", None),
    ],
)
def test_only_a_leading_slash_command_is_a_room_command(
    content: str, command: tuple[ClaudeSDKCommand, str] | None
) -> None:
    assert ClaudeSDKAdapter._extract_command(content) == command
