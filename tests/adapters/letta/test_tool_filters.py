"""Letta MCP registration honors AdapterFeatures.exclude_tools."""

from __future__ import annotations

from band.adapters.letta import LettaAdapter
from band.core.types import Capability
from band.runtime.tools import BandTool, selected_tool_definitions


class TestLettaToolFilters:
    def test_exclude_tools_are_absent_from_mcp_definitions(self) -> None:
        excluded = {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
        adapter = LettaAdapter(
            capabilities={Capability.FILES},
            exclude_tools=excluded,
        )

        names = {definition.name for definition in adapter._mcp._tool_definitions}
        assert names == {
            definition.name
            for definition in selected_tool_definitions(adapter.features)
        }
        assert names.isdisjoint(excluded)
