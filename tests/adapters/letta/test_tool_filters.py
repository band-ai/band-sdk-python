"""Letta MCP attach honors AdapterFeatures.exclude_tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from band.adapters.letta import LettaAdapter, LettaAdapterConfig
from band.core.types import Capability
from band.integrations.letta.config import LettaMCPConfig
from band.runtime.tools import MEMORY_TOOL_NAMES, BandTool


class _FakeToolsList:
    def __init__(self, tools: list[Any]) -> None:
        self._tools = tools

    async def list(self, *, mcp_server_id: str) -> list[Any]:
        assert mcp_server_id
        return list(self._tools)


class _FakeMcpServers:
    def __init__(self, tools: list[Any]) -> None:
        self.tools = _FakeToolsList(tools)

    async def list(self) -> list[Any]:
        return []

    async def create(self, config: Any = None, **kwargs: Any) -> Any:
        name = (
            getattr(config, "server_name", None) or kwargs.get("server_name") or "band"
        )
        return SimpleNamespace(id="srv-ext-1", server_name=name, name=name)


class _FakeClient:
    def __init__(self, tools: list[Any]) -> None:
        self.mcp_servers = _FakeMcpServers(tools)


class TestLettaToolFilters:
    async def test_exclude_tools_are_absent_from_attached_mcp_ids(self) -> None:
        """exclude_tools must drop those names from the ids register() would
        attach, even when external discovery lists the full platform surface."""
        excluded = {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    mode="external", server_url="http://127.0.0.1:9999/mcp"
                )
            ),
            capabilities={Capability.MEMORY, Capability.FILES},
            exclude_tools=excluded,
        )
        discovered = [
            SimpleNamespace(id="t-send", name=BandTool.SEND_MESSAGE),
            SimpleNamespace(id="t-noreply", name=BandTool.NO_REPLY),
            SimpleNamespace(id="t-mem", name=next(iter(MEMORY_TOOL_NAMES))),
        ]

        await adapter._mcp.register(
            _FakeClient(discovered),
            server_name="band",
            server_url="http://127.0.0.1:9999/mcp",
        )

        attached_names = {
            tool.name for tool in discovered if tool.id in adapter._mcp.tool_ids
        }
        assert attached_names.isdisjoint(excluded)
        assert MEMORY_TOOL_NAMES & attached_names
