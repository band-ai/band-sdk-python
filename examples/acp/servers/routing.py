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
    uv run examples/acp/servers/routing.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import ACPServer, BandACPServerAdapter
from band.integrations.acp import run_acp_server
from band.config import load_agent_config
from band.integrations.acp import AgentRouter

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
    adapter = BandACPServerAdapter()
    adapter.set_router(router)

    # Create ACP protocol handler
    server = ACPServer(adapter)

    # Create Band agent (manages WebSocket connection)
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    )

    logger.info("Starting ACP server with routing...")
    logger.info("Slash commands: /codex, /claude, /gemini")
    logger.info("Session modes: code -> codex, research -> gemini")

    # Start platform connection (non-blocking)
    await agent.start()
    try:
        # Block on stdio until editor disconnects
        await run_acp_server(server)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
