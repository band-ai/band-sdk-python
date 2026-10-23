"""Cursor recognizes the same room-command prefix its handler accepts."""

from __future__ import annotations

import pytest

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from band.integrations.acp.cursor import ROOM_COMMAND, CursorCommandWord
from tests.adapters.codex.kit import make_platform_message


class TestCursorControlRecognition:
    @pytest.mark.parametrize(
        ("content", "recognized"),
        [
            (ROOM_COMMAND, True),
            (
                f"@owner/cursor {ROOM_COMMAND.title()} {CursorCommandWord.DECISIONS}",
                True,
            ),
            (f"  {ROOM_COMMAND}\tstatus", True),
            (f"{ROOM_COMMAND} unknown", True),
            (f"{ROOM_COMMAND}-extra", False),
            (f"do not run {ROOM_COMMAND}", False),
            ("", False),
        ],
    )
    def test_recognizes_room_commands(self, content: str, recognized: bool) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent"))

        assert (
            adapter.is_control_message(make_platform_message(content=content))
            is recognized
        )
