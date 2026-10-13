"""Shared helpers for the Codex adapter flow tests."""

from __future__ import annotations

from band.integrations.codex import RpcEvent
from band.runtime.tools import BandTool
from tests.adapters.codexturns import final_text, tool_call_request, turn_completed


def answered_turn() -> list[RpcEvent]:
    """A turn explicitly completed through a Band decline tool."""
    return [
        tool_call_request(1, BandTool.NO_REPLY),
        final_text("Done."),
        turn_completed(),
    ]
