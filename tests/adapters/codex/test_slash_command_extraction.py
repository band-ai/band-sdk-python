"""Codex adapter: ``_extract_local_command`` reads a command only when one leads the message."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapter,
    CodexCommand,
)


class TestSlashCommandExtraction:
    """``_extract_local_command`` reads a command only when one leads the message."""

    @pytest.mark.parametrize(
        "content",
        [
            "@team/bot Please don't /approve req-1 yet",
            "@team/bot do not /approve",
            "@team/bot ignore the /decline suggestion",
            "@team/bot use /tmp as scratch",
        ],
    )
    def test_prose_mentioning_a_command_is_not_a_command(self, content: str) -> None:
        """Prose that argues *against* a command must not invoke it.

        ``/approve`` resolves a pending tool-execution request, and the handler
        takes the first argument token as its id — so a scan that found a slash
        word anywhere in the prefix turned "don't /approve req-1 yet" into an
        approval of ``req-1``.
        """
        assert CodexAdapter._extract_local_command(content) is None

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("/approve req-1", (CodexCommand.APPROVE, "req-1")),
            ("@owner/agent-name /approve req-1", (CodexCommand.APPROVE, "req-1")),
            # Every mentioned participant contributes a token to the block.
            (
                "@owner/agent-name @owner/other-bot /approve req-1",
                (CodexCommand.APPROVE, "req-1"),
            ),
            # Unresolved mentions stay in the platform's normalized @[[uuid]] form.
            (
                "@[[3029eb1d-d998-4567-bdf3-d82fc6b89a58]] /approvals",
                (CodexCommand.APPROVALS, ""),
            ),
            ("@team/bot /approve", (CodexCommand.APPROVE, "")),
            # Any whitespace separates a command from its argument, not just " ".
            ("@team/bot /approve\treq-1", (CodexCommand.APPROVE, "req-1")),
            ("/approve\nreq-1", (CodexCommand.APPROVE, "req-1")),
        ],
    )
    def test_command_behind_the_mention_block_is_recognised(
        self, content: str, expected: tuple[CodexCommand, str]
    ) -> None:
        """The delivery mention block must never hide a real command."""
        assert CodexAdapter._extract_local_command(content) == expected

    @pytest.mark.parametrize("content", ["@team/bot /", "@team/bot /notacommand x", ""])
    def test_non_commands_are_ignored(self, content: str) -> None:
        assert CodexAdapter._extract_local_command(content) is None
