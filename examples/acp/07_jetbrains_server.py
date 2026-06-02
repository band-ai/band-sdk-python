# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
JetBrains ACP Server - Use Band as an ACP agent in JetBrains IDEs.

This example starts Band as an ACP agent that JetBrains IDEs (IntelliJ,
PyCharm, WebStorm, etc.) can connect to via the ACP protocol. When you type
prompts in the JetBrains AI Chat, they are routed to Band platform peers
and responses stream back to the IDE.

Architecture:
    JetBrains IDE (AI Chat)
      -> spawns this process as ACP agent
        -> ACPServer (ACP JSON-RPC over stdio)
          -> BandACPServerAdapter
            -> Band Platform (creates room, sends message)
              -> Peer agents respond via WebSocket
            -> Streams responses back to IDE via session_update

JetBrains Configuration (~/.jetbrains/acp.json):
    {
        "default_mcp_settings": {},
        "agent_servers": {
            "Band": {
                "command": "band-acp",
                "args": ["--agent-id", "YOUR_AGENT_ID"],
                "env": {
                    "BAND_API_KEY": "YOUR_API_KEY"
                }
            }
        }
    }

    Or if running from source:
    {
        "default_mcp_settings": {},
        "agent_servers": {
            "Band": {
                "command": "uv",
                "args": [
                    "run", "--extra", "acp",
                    "band-acp", "--agent-id", "YOUR_AGENT_ID"
                ],
                "env": {
                    "BAND_API_KEY": "YOUR_API_KEY"
                }
            }
        }
    }

Prerequisites:
    1. Install: pip install band-sdk[acp]
    2. Set BAND_API_KEY and BAND_AGENT_ID

Run standalone for testing:
    BAND_API_KEY=... BAND_AGENT_ID=... uv run examples/acp/07_jetbrains_server.py
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
from band.config import load_agent_config
from band.integrations.acp.push_handler import ACPPushHandler
from band.integrations.acp.router import AgentRouter
from band.integrations.acp.server import ACPServer
from band.integrations.acp.server_adapter import BandACPServerAdapter

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")
    # JetBrains IDEs inject credentials via ~/.jetbrains/acp.json env config.
    # Fall back to agent_config.yaml for standalone testing.
    api_key = os.getenv("BAND_API_KEY")

    if not api_key:
        try:
            agent_id, api_key = load_agent_config("jetbrains_acp_agent")
        except Exception:
            raise ValueError(
                "BAND_API_KEY environment variable is required, "
                "or configure 'jetbrains_acp_agent' in agent_config.yaml"
            )
    else:
        agent_id = os.getenv("BAND_AGENT_ID")
        if not agent_id:
            raise ValueError(
                "BAND_AGENT_ID is required. Pass via --agent-id or set BAND_AGENT_ID."
            )

    # Create ACP server adapter
    adapter = BandACPServerAdapter(
        rest_url=rest_url,
        api_key=api_key,
    )

    # Optional: configure routing for slash commands
    # Users can type "/codex fix bug" in the AI Chat to route to a specific peer
    router = AgentRouter(
        slash_commands={
            "codex": "codex",
            "claude": "claude-code",
        },
    )
    adapter.set_router(router)

    # Wire up push handler for real-time activity from other agents
    push_handler = ACPPushHandler(adapter)
    adapter.set_push_handler(push_handler)

    # Create ACP protocol handler
    server = ACPServer(adapter)

    # Create Band agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Band ACP server for JetBrains...")
    logger.info("IDE will connect via stdio ACP protocol.")

    await agent.start()
    try:
        await run_agent(server)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
