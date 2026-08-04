# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
ACP Server with push notifications - Real-time activity from Band peers.

This example demonstrates push notifications: when platform messages arrive
for rooms with active ACP sessions but no pending prompt, they are pushed
to the editor as unsolicited session_update notifications.

This lets the editor display real-time activity from other agents working
in the same Band room, even when the user hasn't sent a prompt.

Architecture:
    Band Platform (peer sends a message in room)
      -> BandACPServerAdapter.on_message() (no pending prompt)
        -> ACPPushHandler.handle_push_event()
          -> EventConverter.convert(msg) -> ACP session_update chunk
            -> acp_client.session_update(session_id, chunk)
              -> Editor shows real-time peer activity

Prerequisites:
    1. Set environment variables:
       - BAND_API_KEY: Your Band API key
       - BAND_WS_URL: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
       - BAND_REST_URL: REST API URL (default: https://app.band.ai)

    2. Have peers configured on the Band platform

Run with:
    uv run examples/acp/servers/push_notifications.py
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
from band.integrations.acp import ACPPushHandler

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

    # Create ACP server adapter
    adapter = BandACPServerAdapter()

    # Wire up push handler for unsolicited session_update notifications
    push_handler = ACPPushHandler(adapter)
    adapter.set_push_handler(push_handler)

    # Create ACP protocol handler
    server = ACPServer(adapter)

    # Create Band agent (manages WebSocket connection)
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    )

    logger.info("Starting ACP server with push notifications...")
    logger.info("Peer activity will be pushed to the editor in real time.")

    # Start platform connection (non-blocking)
    await agent.start()
    try:
        # Block on stdio until editor disconnects
        await run_agent(server)
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
