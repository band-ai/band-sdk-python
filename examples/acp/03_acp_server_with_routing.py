# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
ACP Server with routing - Target specific peers via slash commands or modes.

This example demonstrates how to route editor prompts to specific Band
peers using the AgentRouter. Users can:

  1. Use slash commands: "/codex fix this bug" -> routes to "codex" peer
  2. Set session modes: mode "code" -> routes to configured peer
  3. Default: mention all peers in the room

Architecture:
    Editor prompt "/codex fix bug"
      -> ACPServer.prompt()
        -> AgentRouter.resolve() -> ("fix bug", "codex")
          -> BandACPServerAdapter.handle_prompt(mention=["codex"])
            -> Band Platform (only @codex is mentioned)

Prerequisites:
    1. Set environment variables:
       - BAND_API_KEY: Your Band API key
       - BAND_WS_URL: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
       - BAND_REST_URL: REST API URL (default: https://app.band.ai)

    2. Have peers configured on the Band platform

Run with:
    uv run examples/acp/03_acp_server_with_routing.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from acp import run_agent
from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import ACPServer, BandACPServerAdapter
from band.config import load_agent_config
from band.integrations.acp import AgentRouter

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")
    # ACP server examples check env vars first because editors (Zed, Cursor)
    # typically inject credentials via environment when spawning the subprocess.
    api_key = os.getenv("BAND_API_KEY")

    if not api_key:
        try:
            agent_id, api_key = load_agent_config("acp_server_agent")
        except Exception:
            raise ValueError(
                "BAND_API_KEY environment variable is required, "
                "or configure 'acp_server_agent' in agent_config.yaml"
            )
    else:
        agent_id = os.getenv("BAND_AGENT_ID", "acp-server")

    # Configure routing: slash commands and mode-based routing
    router = AgentRouter(
        slash_commands={
            "codex": "codex",  # /codex <prompt> -> route to "codex" peer
            "claude": "claude",  # /claude <prompt> -> route to "claude" peer
            "gemini": "gemini",  # /gemini <prompt> -> route to "gemini" peer
        },
        mode_to_peer={
            "code": "codex",  # "code" mode -> route to "codex" peer
            "research": "gemini",  # "research" mode -> route to "gemini" peer
        },
    )

    # Create ACP server adapter with routing
    adapter = BandACPServerAdapter(
        rest_url=rest_url,
        api_key=api_key,
    )
    adapter.set_router(router)

    # Create ACP protocol handler
    server = ACPServer(adapter)

    # Create Band agent (manages WebSocket connection)
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting ACP server with routing...")
    logger.info("Slash commands: /codex, /claude, /gemini")
    logger.info("Session modes: code -> codex, research -> gemini")

    # Start platform connection (non-blocking)
    await agent.start()
    try:
        # Block on stdio until editor disconnects
        await run_agent(server)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
