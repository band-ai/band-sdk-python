"""A shell-write policy accepts only the exact requested native command."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.baseline.smoke.samples.approvals import (
    CLAUDE_SHELL_TOOLS,
    NATIVE_SHELL_COMMAND_MISSING,
    UnattendedPolicy,
    marker_command,
)
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall, ToolCalls


class TestShellPolicy:
    @pytest.mark.parametrize("shell", CLAUDE_SHELL_TOOLS)
    def test_host_policy_requires_the_requested_native_command(
        self, shell_policy: UnattendedPolicy, shell: str, tmp_path: Path
    ) -> None:
        target = tmp_path / "policy.txt"
        marker = "policy-marker"
        calls = ToolCalls(
            [
                ToolCall("ToolSearch", {"query": "shell"}),
                ToolCall(shell, {"command": "echo unrelated"}),
                ToolCall(
                    shell,
                    {
                        "command": marker_command(marker, target),
                        "description": "Write file",
                    },
                ),
                ToolCall("band_send_message", {"content": "closed"}),
            ]
        )

        shell_policy.assert_attempted(calls, marker, target)

    @pytest.mark.parametrize("shell", CLAUDE_SHELL_TOOLS)
    @pytest.mark.parametrize(
        "mismatch",
        [
            "empty",
            "unrelated",
            "unsupported",
            "missing-command",
            "marker",
            "target",
            "prefix",
            "suffix",
        ],
    )
    def test_host_policy_rejects_missing_or_different_native_command(
        self, shell_policy: UnattendedPolicy, shell: str, mismatch: str, tmp_path: Path
    ) -> None:
        target = tmp_path / "policy.txt"
        marker = "policy-marker"
        command = marker_command(marker, target)
        match mismatch:
            case "empty":
                calls = ToolCalls()
            case "unrelated":
                calls = ToolCalls(
                    [ToolCall("band_send_message", {"content": "closed"})]
                )
            case "unsupported":
                calls = ToolCalls([ToolCall("OtherShell", {"command": command})])
            case "missing-command":
                calls = ToolCalls([ToolCall(shell)])
            case "marker":
                calls = ToolCalls(
                    [ToolCall(shell, {"command": marker_command("wrong", target)})]
                )
            case "target":
                calls = ToolCalls(
                    [
                        ToolCall(
                            shell,
                            {"command": marker_command(marker, tmp_path / "wrong.txt")},
                        )
                    ]
                )
            case "prefix":
                calls = ToolCalls(
                    [ToolCall(shell, {"command": f"echo before; {command}"})]
                )
            case "suffix":
                calls = ToolCalls(
                    [ToolCall(shell, {"command": f"{command}; echo after"})]
                )
            case _:
                raise ValueError(f"Unknown mismatch: {mismatch}")

        with pytest.raises(AssertionError, match=NATIVE_SHELL_COMMAND_MISSING):
            shell_policy.assert_attempted(calls, marker, target)
