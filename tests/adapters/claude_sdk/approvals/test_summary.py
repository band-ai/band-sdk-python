from __future__ import annotations

from typing import Any

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "summary"),
    [
        ("Bash", {"command": "ls -la /home/user"}, "Bash: `ls -la /home/user`"),
        ("Edit", {"file_path": "/src/main.py"}, "Edit: /src/main.py"),
        ("SomeTool", {}, "SomeTool"),
    ],
)
def test_an_approval_names_what_the_tool_will_touch(
    tool_name: str, tool_input: dict[str, Any], summary: str
) -> None:
    assert ClaudeSDKAdapter._approval_summary(tool_name, tool_input) == summary


@pytest.mark.parametrize(
    ("command", "secret"),
    [
        ("curl -H token=sk-abc123 https://api.example.com", "sk-abc123"),
        ("mysql -u root password=s3cret db", "s3cret"),
    ],
)
def test_an_approval_never_posts_a_secret_to_the_room(
    command: str, secret: str
) -> None:
    summary = ClaudeSDKAdapter._approval_summary("Bash", {"command": command})
    assert secret not in summary
    assert "***" in summary
