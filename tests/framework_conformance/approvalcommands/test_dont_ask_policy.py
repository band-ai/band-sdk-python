"""The dont-ask policy accepts only a write tool call matching the request."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e.baseline.smoke.samples.approvals import (
    UNATTENDED_POLICIES,
    UnattendedPolicy,
    marker_command,
)
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall, ToolCalls

WriteCalls = Callable[[str, Path], ToolCalls]


def _dont_ask_policy() -> UnattendedPolicy:
    return next(policy for policy in UNATTENDED_POLICIES if policy.name == "dont-ask")


def _shell_instead_of_write(marker: str, target: Path) -> ToolCalls:
    return ToolCalls([ToolCall("Bash", {"command": marker_command(marker, target)})])


def _write_wrong_content(marker: str, target: Path) -> ToolCalls:
    return ToolCalls(
        [ToolCall("Write", {"file_path": str(target), "content": "wrong"})]
    )


def _write_wrong_path(marker: str, target: Path) -> ToolCalls:
    return ToolCalls(
        [
            ToolCall(
                "Write",
                {"file_path": str(target.with_name("wrong.txt")), "content": marker},
            )
        ]
    )


class TestDontAskPolicy:
    def test_dont_ask_requires_a_write_tool_attempt(self, tmp_path: Path) -> None:
        target = tmp_path / "policy.txt"
        marker = "policy-marker"
        _dont_ask_policy().assert_attempted(
            ToolCalls(
                [ToolCall("Write", {"file_path": str(target), "content": marker})]
            ),
            marker,
            target,
        )

    @pytest.mark.parametrize(
        ("build_calls", "error"),
        [
            (_shell_instead_of_write, "expected tool 'Write'"),
            (_write_wrong_content, "matched args"),
            (_write_wrong_path, "matched args"),
        ],
        ids=["shell-instead", "wrong-content", "wrong-path"],
    )
    def test_dont_ask_rejects_a_write_that_misses_the_request(
        self, build_calls: WriteCalls, error: str, tmp_path: Path
    ) -> None:
        target = tmp_path / "policy.txt"
        marker = "policy-marker"
        with pytest.raises(AssertionError, match=error):
            _dont_ask_policy().assert_attempted(
                build_calls(marker, target), marker, target
            )
