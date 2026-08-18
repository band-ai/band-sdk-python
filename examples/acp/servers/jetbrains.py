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
    BAND_API_KEY=... BAND_AGENT_ID=... uv run examples/acp/servers/jetbrains.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.config import load_agent_config
from band.integrations.acp.push_handler import ACPPushHandler
from band.integrations.acp.router import AgentRouter
from band.integrations.acp.server import ACPServer, run_acp_server
from band.integrations.acp.server_adapter import BandACPServerAdapter

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
    band_agent_id: str = ""


async def main() -> None:
    load_dotenv()
    settings = Settings()

    # JetBrains IDEs inject credentials via ~/.jetbrains/acp.json env config.
    # Fall back to agent_config.yaml for standalone testing.
    api_key = settings.band_api_key

    if not api_key:
        try:
            agent_id, api_key = load_agent_config("jetbrains_acp_agent")
        except Exception:
            raise ValueError(
                "BAND_API_KEY environment variable is required, "
                "or configure 'jetbrains_acp_agent' in agent_config.yaml"
            )
    else:
        if not settings.band_agent_id:
            raise ValueError(
                "BAND_AGENT_ID is required. Pass via --agent-id or set BAND_AGENT_ID."
            )
        agent_id = settings.band_agent_id

    # Create ACP server adapter
    adapter = BandACPServerAdapter()

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
    )

    logger.info("Starting Band ACP server for JetBrains...")
    logger.info("IDE will connect via stdio ACP protocol.")

    await agent.start()
    try:
        await run_acp_server(server)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
