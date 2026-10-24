"""Codex keeps local commands separate from ordinary model prompts."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapter, CodexCommand
from band.testing import FakeAgentTools
from tests.adapters.codex.kit import make_platform_message


class TestCodexControlRecognition:
    @pytest.mark.parametrize(
        ("content", "recognized"),
        [
            (f"/{CodexCommand.APPROVE} req-1", True),
            (f"@owner/codex /{CodexCommand.STATUS}", True),
            (f"/{CodexCommand.MODEL}", True),
            (f"/{CodexCommand.SANDBOX} workspace-write", True),
            (f"do not /{CodexCommand.APPROVE} req-1", False),
            ("/unknown", False),
            ("", False),
        ],
    )
    def test_recognizes_commands_handled_locally(
        self, content: str, recognized: bool
    ) -> None:
        adapter = CodexAdapter()

        assert (
            adapter.is_control_message(make_platform_message(content=content))
            is recognized
        )

    async def test_recognizes_commands_not_consumed_by_control_handler(self) -> None:
        adapter = CodexAdapter()
        msg = make_platform_message(content=f"/{CodexCommand.MODEL}")

        assert adapter.is_control_message(msg)
        assert not await adapter.try_handle_control(msg, FakeAgentTools())
