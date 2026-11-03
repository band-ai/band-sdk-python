"""OpenCode MCP registration honors AdapterFeatures.exclude_tools."""

from __future__ import annotations

from band.adapters.opencode import OpencodeAdapter
from band.core.types import Capability
from band.runtime.tools import BandTool, selected_tool_definitions
from tests.adapters.opencode.helpers import FakeOpencodeClient


class TestOpencodeToolFilters:
    def test_exclude_tools_are_absent_from_mcp_definitions(self) -> None:
        excluded = {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
        adapter = OpencodeAdapter(
            client_factory=lambda _config: FakeOpencodeClient(),
            capabilities={Capability.MEMORY, Capability.CONTACTS},
            exclude_tools=excluded,
        )

        names = {definition.name for definition in adapter._tool_definitions}
        assert names == {
            definition.name
            for definition in selected_tool_definitions(adapter.features)
        }
        assert names.isdisjoint(excluded)
        assert names <= adapter._own_tool_names
