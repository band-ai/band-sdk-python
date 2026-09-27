"""Approval probes must write the same marker through each CI host shell."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from band.adapters.opencode.approvals import APPROVAL_REQUESTED_TEMPLATE
from band.client.streaming import MessageCreatedPayload
from tests.e2e.baseline.smoke.samples.approvals import (
    DIALECTS,
    Notice,
    appending_command,
    marker_command,
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

    # cmd.exe includes the space before redirection in echo's output.
    expected = f"{marker} " if sys.platform == "win32" else marker
    assert target.read_text(encoding="utf-8").splitlines() == [expected, expected]

    if sys.platform == "win32":
        subprocess.run(
            ["pwsh", "-NoProfile", "-Command", marker_command(marker, target)],
            check=True,
            cwd=tmp_path,
        )
        subprocess.run(
            ["pwsh", "-NoProfile", "-Command", appending_command(marker, target)],
            check=True,
            cwd=tmp_path,
        )
        assert target.read_text(encoding="utf-8").splitlines() == [marker, marker]


def test_approval_closure_requires_reply_after_the_last_request_and_notice() -> None:
    dialect = DIALECTS[Adapter.OPENCODE]
    closing_reply = "turn-finished"
    request = APPROVAL_REQUESTED_TEMPLATE.format(
        permission="bash", patterns="cat approval.txt", request_id="follow-up"
    )
    notice = Notice("OpenCode approval `follow-up` handled with `reject`.")

    def message(content: str) -> MessageCreatedPayload:
        return MessageCreatedPayload(
            id=content,
            content=content,
            message_type="text",
            sender_id="agent",
            sender_type="Agent",
            inserted_at="2026-09-27T00:00:00Z",
            updated_at="2026-09-27T00:00:00Z",
        )

    before_close = [
        message("Checking the result."),
        message(request),
        message(notice.text),
    ]
    assert not dialect.settled(before_close, notice, closing_reply=closing_reply)
    assert not dialect.settled(
        [*before_close, message("Checking the next command.")],
        notice,
        closing_reply=closing_reply,
    )
    assert not dialect.settled(
        [*before_close, message(f"I will finish with {closing_reply} after checking.")],
        notice,
        closing_reply=closing_reply,
    )
    assert not dialect.settled(
        [*before_close, message("OpenCode completed the turn without a text reply.")],
        notice,
        closing_reply=closing_reply,
    )
    assert dialect.settled(
        [*before_close, message(f"@[[agent]] {closing_reply}")],
        notice,
        closing_reply=closing_reply,
    )
