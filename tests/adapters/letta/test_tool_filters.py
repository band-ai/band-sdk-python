"""Letta MCP attach honors AdapterFeatures.exclude_tools."""

from __future__ import annotations

from types import SimpleNamespace

from band.adapters.letta import LettaAdapter, LettaAdapterConfig
from band.core.types import Capability
from band.integrations.letta.config import LettaMCPConfig
from band.integrations.letta.prompts import (
    SEND_EVENT_TOOL_NAMES,
    SEND_MESSAGE_TOOL_NAMES,
)
from band.runtime.tools import BandTool
from tests.adapters.letta.kit import FakeLettaMcpClient


class TestLettaToolFilters:
    async def test_exclude_tools_are_absent_from_attached_mcp_ids(self) -> None:
        """exclude_tools must drop those names from the ids register() would
        attach, even when external discovery lists the full platform surface."""
        excluded = {BandTool.SEND_MESSAGE, BandTool.NO_REPLY}
        memory = BandTool.LIST_MEMORIES
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
            SimpleNamespace(id="t-mem", name=memory),
        ]

        await adapter._mcp.register(
            FakeLettaMcpClient(discovered),
            server_name="band",
            server_url="http://127.0.0.1:9999/mcp",
        )

        attached_names = {
            tool.name for tool in discovered if tool.id in adapter._mcp.tool_ids
        }
        assert attached_names == {memory}

    async def test_legacy_send_aliases_attach_when_send_message_is_allowed(
        self,
    ) -> None:
        """External discovery may only list create_agent_chat_* send/event
        names; those aliases must still attach when band_send_message stays
        allowed."""
        adapter = LettaAdapter(
            config=LettaAdapterConfig(
                mcp=LettaMCPConfig(
                    mode="external", server_url="http://127.0.0.1:9999/mcp"
                )
            ),
            capabilities={Capability.MEMORY},
        )
        legacy_send = SEND_MESSAGE_TOOL_NAMES[1]
        legacy_event = SEND_EVENT_TOOL_NAMES[1]
        discovered = [
            SimpleNamespace(id="t-legacy-send", name=legacy_send),
            SimpleNamespace(id="t-legacy-event", name=legacy_event),
            SimpleNamespace(id="t-mem", name=BandTool.LIST_MEMORIES),
        ]

        await adapter._mcp.register(
            FakeLettaMcpClient(discovered),
            server_name="band",
            server_url="http://127.0.0.1:9999/mcp",
        )

        assert set(adapter._mcp.tool_ids) == {
            "t-legacy-send",
            "t-legacy-event",
            "t-mem",
        }
        assert adapter._mcp.send_message_tool == legacy_send
        assert adapter._mcp.send_event_tool == legacy_event
