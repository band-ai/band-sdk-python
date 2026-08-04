# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic ACP Server example - Band as an ACP agent.

This example starts Band as an ACP agent that editors (Zed, Cursor,
JetBrains, Neovim) can connect to. It implements the "Super-Agent" pattern:
a single ACP facade that routes editor requests to multiple Band peers.

Architecture:
    Editor (Zed/Cursor/JetBrains/Neovim)
      -> ACP JSON-RPC over stdio
        -> ACPServer (protocol handler)
          -> BandACPServerAdapter (platform bridge)
            -> Band Platform (REST + WebSocket)
              -> Multi-agent responses via Phoenix Channels
            -> ACP session_update notifications back to editor

Prerequisites:
    1. Set environment variables:
       - BAND_API_KEY: Your Band API key
       - BAND_WS_URL: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
       - BAND_REST_URL: REST API URL (default: https://app.band.ai)

    2. Have peers configured on the Band platform

Editor Configuration:
    Zed (settings.json):
        {"agent_servers": {"Band": {"type": "custom", "command": "uv run examples/acp/servers/basic.py"}}}

    JetBrains (~/.jetbrains/acp.json):
        {"agent_servers": {"Band": {"command": "band-acp", "args": ["--agent-id", "..."], "env": {"BAND_API_KEY": "..."}}}}

Run with:
    uv run examples/acp/servers/basic.py
"""

from __future__ import annotations

import asyncio
import logging

from acp import run_agent
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import ACPServer, BandACPServerAdapter
from band.config import load_agent_config

configure_logging(
    level=logging.INFO,
    root_level=logging.INFO,
    extra_loggers={
        "httpcore": logging.WARNING,
        "httpx": logging.WARNING,
    },
)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    band_api_key: str = ""
    band_agent_id: str = "acp-server"


async def main() -> None:
    load_dotenv()
    settings = Settings()

    # ACP server examples check env vars first because editors (Zed, Cursor)
    # typically inject credentials via environment when spawning the subprocess.
    api_key = settings.band_api_key

    if not api_key:
        try:
            agent_id, api_key = load_agent_config("acp_server_agent")
        except Exception:
            raise ValueError(
                "BAND_API_KEY environment variable is required, "
                "or configure 'acp_server_agent' in agent_config.yaml"
            )
    else:
        agent_id = settings.band_agent_id

    # Create ACP server adapter with direct REST client
    adapter = BandACPServerAdapter()

    # Create ACP protocol handler
    server = ACPServer(adapter)

    # Create Band agent (manages WebSocket connection)
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    )

    logger.info("Starting ACP server (Band as ACP agent)...")
    logger.info("Waiting for editor to connect via stdio...")

    # Start platform connection (non-blocking)
    await agent.start()
    try:
        # Block on stdio until editor disconnects
        await run_agent(server)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
