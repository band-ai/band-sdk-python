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
    uv run examples/acp/05_acp_server_push_notifications.py
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
from band.integrations.acp import ACPPushHandler

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

    # Create ACP server adapter
    adapter = BandACPServerAdapter(
        rest_url=rest_url,
        api_key=api_key,
    )

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
        ws_url=ws_url,
        rest_url=rest_url,
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
