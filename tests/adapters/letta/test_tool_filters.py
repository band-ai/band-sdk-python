"""Letta MCP bridge construction honors AdapterFeatures.exclude_tools."""

from __future__ import annotations

from band.adapters.letta import LettaAdapter
from band.core.types import Capability
from band.runtime.tools import MEMORY_TOOL_NAMES, BandTool


class TestLettaToolFilters:
    def test_exclude_tools_are_absent_from_mcp_definitions(self) -> None:
        """exclude_tools must drop those names from the bridge's MCP defs
        while leaving capability-enabled platform tools registered."""
        excluded = {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
        adapter = LettaAdapter(
            capabilities={Capability.MEMORY, Capability.FILES},
            exclude_tools=excluded,
        )

        names = {definition.name for definition in adapter._mcp._tool_definitions}
        assert names.isdisjoint(excluded)
        assert MEMORY_TOOL_NAMES <= names
