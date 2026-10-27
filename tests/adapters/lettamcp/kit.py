"""Shared client and backend peers for Letta MCP tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import httpx
from letta_client import NotFoundError

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
)
from band.integrations.letta.mcp import LettaMCPBridge
from band.integrations.mcp import BandMCPTransport
from band.runtime.tools import BandTool
from tests.adapters.lettakit import (
    letta_mcp_servers_api,
    make_mock_mcp_server,
    make_mock_mcp_tool,
)
from tests.mcpbackends import hold_backend
from tests.mcpclient import served_tool_names

LETTA_MCP_SERVERS_URL = "http://letta.test/v1/mcp-servers/"


def letta_not_found(message: str) -> NotFoundError:
    """The 404 letta-client raises when a tool id or MCP server row no longer
    exists in the organization."""
    return NotFoundError(
        message,
        response=httpx.Response(
            404, request=httpx.Request("PATCH", LETTA_MCP_SERVERS_URL)
        ),
        body={"detail": message},
    )


def letta_registering_one_tool() -> AsyncMock:
    """A Letta client that registers ``mcp-server-1`` exposing one send tool."""
    client = AsyncMock()
    client.mcp_servers = letta_mcp_servers_api()
    client.mcp_servers.list.return_value = []
    client.mcp_servers.create.return_value = make_mock_mcp_server("mcp-server-1")
    client.mcp_servers.tools.list.return_value = [
        make_mock_mcp_tool("t1", BandTool.SEND_MESSAGE)
    ]
    return client


@asynccontextmanager
async def registered_on_real_server(
    config: LettaAdapterConfig | None = None,
) -> AsyncIterator[tuple[LettaMCPBridge, AsyncMock]]:
    """A self-hosted bridge registered with a mocked Letta, serving a real Band
    MCP server -- the Letta server is the only boundary not run for real."""
    client = letta_registering_one_tool()
    bridge = LettaAdapter(config=config)._mcp
    async with bridge.backend:
        await bridge.ensure_ready(client)
        yield bridge, client


async def registered_on_a_dead_fake_server() -> tuple[LettaMCPBridge, AsyncMock]:
    """A self-hosted bridge registered with a mocked Letta at a fake server
    that has since died, for flows that never dial it."""
    client = letta_registering_one_tool()
    bridge = LettaAdapter()._mcp
    dead = await hold_backend(bridge.backend)
    await bridge.ensure_ready(client)
    dead.is_running = False
    return bridge, client


def registered_configs(client: AsyncMock) -> list[dict[str, Any]]:
    """Every registration config handed to Letta, creates then updates."""
    calls = [
        *client.mcp_servers.create.await_args_list,
        *client.mcp_servers.update.await_args_list,
    ]
    return [call.kwargs["config"] for call in calls]


def registered_urls(client: AsyncMock) -> list[str]:
    """Every URL handed to Letta, creates then updates."""
    return [config["server_url"] for config in registered_configs(client)]


async def serves_band_tools(config: dict[str, Any]) -> bool:
    """Whether a registration ``config``, dialed as Letta dials it, reaches
    the Band tools."""
    served = await served_tool_names(
        config["server_url"], BandMCPTransport.SSE, headers=config["custom_headers"]
    )
    return BandTool.SEND_MESSAGE in served
