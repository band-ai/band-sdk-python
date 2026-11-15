"""Letta tool-name aliases retain their canonical BandTool identity."""

from __future__ import annotations

from band.integrations.letta.prompts import (
    SEND_EVENT_TOOL_NAMES,
    SEND_MESSAGE_TOOL_NAMES,
)
from band.runtime.tools import BandTool


class TestLettaToolNames:
    def test_send_tool_names_use_band_tool_enum_member(self) -> None:
        """SEND_MESSAGE_TOOL_NAMES/SEND_EVENT_TOOL_NAMES's canonical entry must be
        a BandTool member, not a hardcoded literal duplicate that could silently
        drift from BandTool if its value ever changed."""
        assert isinstance(SEND_MESSAGE_TOOL_NAMES[0], BandTool)
        assert isinstance(SEND_EVENT_TOOL_NAMES[0], BandTool)
