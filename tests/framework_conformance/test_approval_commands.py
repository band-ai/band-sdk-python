"""Approval probes must write the same marker through each CI host shell."""

from __future__ import annotations

import subprocess
import sys
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
    DIALECTS,
    Notice,
    appending_command,
    marker_command,
    written_lines,
)
from tests.e2e.baseline.toolkit.adapters import Adapter


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
