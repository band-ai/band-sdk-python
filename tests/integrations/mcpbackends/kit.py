"""Shared settings for Band MCP backend tests."""

from __future__ import annotations

from band.integrations.mcp import (
    BandMCPBackendSettings,
)
from band.runtime.tools import iter_tool_definitions

ONE_TOOL = next(iter(iter_tool_definitions()))


def one_tool_settings() -> BandMCPBackendSettings:
    """A real server on the default port range."""
    return BandMCPBackendSettings(
        tool_definitions=[ONE_TOOL],
        get_tools=lambda _room_id: None,
    )
