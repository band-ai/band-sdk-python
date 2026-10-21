"""Tests for local Band MCP injection."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from band.core.types import Capability
from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from band.integrations.mcp import BandMCPTransport
from band.runtime.formatters import SENDER_METADATA_INSTRUCTION
from band.runtime.prompts import COMMUNICATION_INSTRUCTIONS
from tests.integrations.acp.clientadapter.kit import CODEX
from tests.mcpbackends import backends_created_by
from tests.mcpclient import endpoint_path


class TestACPClientAdapterLocalMcpConfig:
    """Tests for local Band MCP injection."""

    @pytest.mark.asyncio
    async def test_get_or_start_band_mcp_server_returns_http_config(self) -> None:
        """Should expose the room's endpoint on the shared HTTP MCP server."""
        adapter = ACPClientAdapter(CODEX)

        try:
            server = await adapter._get_or_start_band_mcp_server(
                "room-1", BandMCPTransport.HTTP
            )
        finally:
            await adapter.cleanup_all()

        assert server.name == "band"
        assert urlsplit(server.url).path == endpoint_path(room_id="room-1")
        assert server.headers == []
        assert server.type == "http"

    @pytest.mark.asyncio
    async def test_get_or_start_band_mcp_server_returns_sse_config(self) -> None:
        """Should expose shared SSE when the ACP agent only supports SSE MCP."""
        adapter = ACPClientAdapter(CODEX)

        try:
            server = await adapter._get_or_start_band_mcp_server(
                "room-1", BandMCPTransport.SSE
            )
        finally:
            await adapter.cleanup_all()

        assert server.name == "band"
        assert urlsplit(server.url).path == endpoint_path(
            BandMCPTransport.SSE, room_id="room-1"
        )
        assert server.headers == []
        assert server.type == "sse"

    @pytest.mark.asyncio
    async def test_get_or_start_band_mcp_server_reuses_shared_server(self) -> None:
        """Should start the shared Band MCP server only once."""
        adapter = ACPClientAdapter(CODEX)

        with backends_created_by() as starts:
            first = await adapter._get_or_start_band_mcp_server(
                "room-1", BandMCPTransport.HTTP
            )
            second = await adapter._get_or_start_band_mcp_server(
                "room-1", BandMCPTransport.HTTP
            )

        assert first.url == second.url
        assert len(starts.requested) == 1

    async def _registered_tool_names(self, adapter: ACPClientAdapter) -> set[str]:
        """The tool names the adapter asks its Band MCP backend to serve."""
        with backends_created_by() as starts:
            await adapter._get_or_start_band_mcp_server("room-1", BandMCPTransport.HTTP)
        return {d.name for d in starts.requested[0].tool_definitions}

    @pytest.mark.asyncio
    async def test_memory_tools_registered_when_declared(self) -> None:
        """Declared MEMORY capability puts its tool group on the loopback server."""
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command="codex"), capabilities=Capability.MEMORY
        )
        assert "band_store_memory" in await self._registered_tool_names(adapter)

    @pytest.mark.asyncio
    async def test_memory_tools_absent_without_declaration(self) -> None:
        """Undeclared MEMORY keeps its tool group off the server (an
        enterprise feature the adapter must opt into)."""
        registered = await self._registered_tool_names(ACPClientAdapter(CODEX))
        assert "band_store_memory" not in registered
        assert "band_send_message" in registered

    @pytest.mark.asyncio
    async def test_contact_tools_registered_regardless_of_declaration(self) -> None:
        """Contact tools stay unconditionally registered — the pre-existing
        default every caller without ``features=`` (every ACP example) relies
        on. Only memory is capability-gated."""
        registered = await self._registered_tool_names(ACPClientAdapter(CODEX))
        assert "band_list_contacts" in registered

    def test_build_system_context_mentions_band_tools(self) -> None:
        """Should keep ACP system context minimal and room-aware."""
        adapter = ACPClientAdapter(CODEX)
        adapter.agent_name = "ACP Bridge"
        adapter.agent_description = "Bridge to ACP agents"
        system_context = adapter._build_system_context("room-123")

        assert "Band tools" in system_context
        assert COMMUNICATION_INSTRUCTIONS in system_context
        assert "reply exactly once" not in system_context
        assert "Never both" not in system_context
        assert "chat_id" not in system_context
        assert "requester" not in system_context
        assert SENDER_METADATA_INSTRUCTION in system_context
        assert "Use each MCP tool's schema" in system_context

    def test_build_system_context_defers_to_external_mcp_tool_schema(self) -> None:
        """The room value is supplied without assuming a remote tool's field name."""
        adapter = ACPClientAdapter(
            ACPClientAdapterConfig(command="codex", inject_band_tools=False)
        )
        adapter.agent_name = "ACP Bridge"
        adapter.agent_description = "Bridge to ACP agents"
        system_context = adapter._build_system_context("room-123")

        assert "Use each MCP tool's schema" in system_context
        assert "Current chat_id: room-123" in system_context
        assert "must include room_id" not in system_context
