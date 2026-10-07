"""Approval probes must write the same marker through each CI host shell."""

from __future__ import annotations

import codecs
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE as CLAUDE_REQUESTED,
)
from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.codex import APPROVAL_REQUESTED_TEMPLATE as CODEX_REQUESTED
from band.adapters.codex import CodexAdapter
from band.adapters.opencode.approvals import APPROVAL_REQUESTED_TEMPLATE
from band.client.streaming import MessageCreatedPayload
from band.integrations.acp.cursor import PERMISSION_REQUESTED_TEMPLATE
from band.integrations.codex.types import CodexApprovalMethod
from tests.e2e.baseline.smoke.samples.approvals import (
    CLAUDE_SHELL_TOOLS,
    DIALECTS,
    NATIVE_SHELL_COMMAND_MISSING,
    UNATTENDED_POLICIES,
    Notice,
    UnattendedPolicy,
    appending_command,
    assert_shell_write_attempted,
    marker_command,
    written_lines,
)
from tests.e2e.baseline.toolkit.adapters import Adapter
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall, ToolCalls

SHELL_POLICIES = tuple(
    policy
    for policy in UNATTENDED_POLICIES
    if policy.assert_attempted is assert_shell_write_attempted
)

WriteCalls = Callable[[str, Path], ToolCalls]


@pytest.fixture(params=SHELL_POLICIES, ids=lambda policy: policy.name)
def shell_policy(request: pytest.FixtureRequest) -> UnattendedPolicy:
    return request.param


@pytest.mark.parametrize("shell", CLAUDE_SHELL_TOOLS)
def test_host_policy_requires_the_requested_native_command(
    shell_policy: UnattendedPolicy, shell: str, tmp_path: Path
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
    shell_policy: UnattendedPolicy, shell: str, mismatch: str, tmp_path: Path
) -> None:
    target = tmp_path / "policy.txt"
    marker = "policy-marker"
    command = marker_command(marker, target)
    match mismatch:
        case "empty":
            calls = ToolCalls()
        case "unrelated":
            calls = ToolCalls([ToolCall("band_send_message", {"content": "closed"})])
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
            calls = ToolCalls([ToolCall(shell, {"command": f"echo before; {command}"})])
        case "suffix":
            calls = ToolCalls([ToolCall(shell, {"command": f"{command}; echo after"})])
        case _:
            raise ValueError(f"Unknown mismatch: {mismatch}")

    with pytest.raises(AssertionError, match=NATIVE_SHELL_COMMAND_MISSING):
        shell_policy.assert_attempted(calls, marker, target)


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


def test_dont_ask_requires_a_write_tool_attempt(tmp_path: Path) -> None:
    target = tmp_path / "policy.txt"
    marker = "policy-marker"
    _dont_ask_policy().assert_attempted(
        ToolCalls([ToolCall("Write", {"file_path": str(target), "content": marker})]),
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
    build_calls: WriteCalls, error: str, tmp_path: Path
) -> None:
    target = tmp_path / "policy.txt"
    marker = "policy-marker"
    with pytest.raises(AssertionError, match=error):
        _dont_ask_policy().assert_attempted(build_calls(marker, target), marker, target)


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [("utf-8", b""), ("utf-8", codecs.BOM_UTF8), ("utf-16-le", codecs.BOM_UTF16_LE)],
    ids=["utf8", "utf8-bom", "utf16-le-bom"],
)
def test_shell_redirect_readback_handles_host_encodings(
    tmp_path: Path, encoding: str, bom: bytes
) -> None:
    target = tmp_path / "policy.txt"
    marker = "policy-marker"
    target.write_bytes(bom + f"{marker} \r\n".encode(encoding))

    assert written_lines(target) == [marker]


def test_approval_commands_write_and_append_with_host_shell(tmp_path: Path) -> None:
    target = tmp_path / "approval with spaces.txt"
    marker = "approval-marker"
    target.write_text("old", encoding="utf-8")

    subprocess.run(marker_command(marker, target), shell=True, check=True, cwd=tmp_path)
    subprocess.run(
        appending_command(marker, target), shell=True, check=True, cwd=tmp_path
    )

    assert written_lines(target) == [marker, marker]

    if sys.platform == "win32":
        # pwsh writes UTF-8; Windows PowerShell 5.1 writes UTF-16 with a BOM.
        for shell in ("pwsh", "powershell"):
            for command in (marker_command, appending_command):
                subprocess.run(
                    [shell, "-NoProfile", "-Command", command(marker, target)],
                    check=True,
                    cwd=tmp_path,
                )
            assert written_lines(target) == [marker, marker], shell


GATED_COMMAND = "cat approval.txt"


def _message(content: str) -> MessageCreatedPayload:
    return MessageCreatedPayload(
        id=content,
        content=content,
        message_type="text",
        sender_id="agent",
        sender_type="Agent",
        inserted_at="2026-09-27T00:00:00Z",
        updated_at="2026-09-27T00:00:00Z",
    )


@pytest.mark.parametrize(
    ("adapter", "request_text"),
    [
        (
            Adapter.CLAUDE_SDK,
            CLAUDE_REQUESTED.format(
                summary=ClaudeSDKAdapter._approval_summary(
                    "Bash", {"command": GATED_COMMAND}
                ),
                token="a-1",
            ),
        ),
        (
            Adapter.CODEX,
            CODEX_REQUESTED.format(
                summary=CodexAdapter._approval_summary(
                    CodexApprovalMethod.COMMAND_EXECUTION, {"command": GATED_COMMAND}
                ),
                token="1",
            ),
        ),
        (
            Adapter.CURSOR_ACP,
            PERMISSION_REQUESTED_TEMPLATE.format(
                tool=GATED_COMMAND, token="p-1", options="allow-once, reject-once"
            ),
        ),
        (
            Adapter.OPENCODE,
            APPROVAL_REQUESTED_TEMPLATE.format(
                permission="bash", patterns=GATED_COMMAND, request_id="per_1"
            ),
        ),
    ],
)
def test_each_dialect_reads_the_command_its_request_gates(
    adapter: Adapter, request_text: str
) -> None:
    dialect = DIALECTS[adapter]
    request = dialect.find_request([_message(request_text)])
    assert request is not None
    assert dialect.shell_command(request) == GATED_COMMAND


def test_approval_closure_requires_reply_after_the_last_request_and_notice() -> None:
    dialect = DIALECTS[Adapter.OPENCODE]
    closing_reply = "turn-finished"
    request = APPROVAL_REQUESTED_TEMPLATE.format(
        permission="bash", patterns="cat approval.txt", request_id="follow-up"
    )
    notice = Notice("OpenCode approval `follow-up` handled with `reject`.")

    before_close = [
        _message("Checking the result."),
        _message(request),
        _message(notice.text),
    ]
    assert not dialect.settled(before_close, notice, closing_reply=closing_reply)
    assert not dialect.settled(
        [*before_close, _message("Checking the next command.")],
        notice,
        closing_reply=closing_reply,
    )
    assert not dialect.settled(
        [
            *before_close,
            _message(f"I will finish with {closing_reply} after checking."),
        ],
        notice,
        closing_reply=closing_reply,
    )
    for closing in (
        f"@[[agent]] {closing_reply}",
        f"`{closing_reply}`",
        f"The command was declined. {closing_reply}",
    ):
        assert dialect.settled(
            [*before_close, _message(closing)], notice, closing_reply=closing_reply
        ), closing
