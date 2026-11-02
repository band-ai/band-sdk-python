"""A shell-write policy accepts only the exact requested native command."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e.baseline.smoke.samples.approvals import (
    CLAUDE_SHELL_TOOLS,
    NATIVE_SHELL_COMMAND_MISSING,
    UnattendedPolicy,
    marker_command,
)
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall, ToolCalls

MismatchCalls = Callable[[str, str, Path], ToolCalls]


def _shell_call(shell: str, command: str) -> ToolCalls:
    return ToolCalls([ToolCall(shell, {"command": command})])


MISMATCHES: dict[str, MismatchCalls] = {
    "empty": lambda shell, marker, target: ToolCalls(),
    "unrelated": lambda shell, marker, target: ToolCalls(
        [ToolCall("band_send_message", {"content": "closed"})]
    ),
    "unsupported": lambda shell, marker, target: _shell_call(
        "OtherShell", marker_command(marker, target)
    ),
    "missing-command": lambda shell, marker, target: ToolCalls([ToolCall(shell)]),
    "marker": lambda shell, marker, target: _shell_call(
        shell, marker_command("wrong", target)
    ),
    "target": lambda shell, marker, target: _shell_call(
        shell, marker_command(marker, target.with_name("wrong.txt"))
    ),
    "prefix": lambda shell, marker, target: _shell_call(
        shell, f"echo before; {marker_command(marker, target)}"
    ),
    "suffix": lambda shell, marker, target: _shell_call(
        shell, f"{marker_command(marker, target)}; echo after"
    ),
}


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
    @pytest.mark.parametrize("build_calls", MISMATCHES.values(), ids=MISMATCHES.keys())
    def test_host_policy_rejects_missing_or_different_native_command(
        self,
        shell_policy: UnattendedPolicy,
        shell: str,
        build_calls: MismatchCalls,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "policy.txt"
        marker = "policy-marker"
        with pytest.raises(AssertionError, match=NATIVE_SHELL_COMMAND_MISSING):
            shell_policy.assert_attempted(
                build_calls(shell, marker, target), marker, target
            )
