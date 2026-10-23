"""Cursor recognizes the same room-command prefix its handler accepts."""

from __future__ import annotations

import pytest

from band.adapters.cursor_acp import CursorACPAdapter, CursorACPAdapterConfig
from tests.adapters.codex.kit import make_platform_message


class TestCursorControlRecognition:
    @pytest.mark.parametrize(
        ("content", "recognized"),
        [
            ("/cursor", True),
            ("@owner/cursor /CuRsOr decisions", True),
            ("  /cursor\tstatus", True),
            ("/cursor unknown", True),
            ("/cursor-extra", False),
            ("do not run /cursor", False),
            ("", False),
        ],
    )
    def test_recognizes_room_commands(self, content: str, recognized: bool) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(command="fake-agent"))

        assert (
            adapter.is_control_message(make_platform_message(content=content))
            is recognized
        )
