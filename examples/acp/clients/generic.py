# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
ACP Client example - Use a remote ACP agent from Band.

This example connects to a remote ACP-compliant agent (Codex CLI, Gemini CLI,
Claude Code, Goose, etc.) and makes it available as a Band platform agent.
Messages from the platform are forwarded to the ACP agent, and responses are
posted back to the chat.

Architecture:
    Band Platform (message arrives in room)
      -> ACPClientAdapter
        -> remote ACP agent subprocess/session
          -> Remote ACP Agent (Codex CLI, Gemini CLI, etc.)
            -> session_update responses streamed back
        -> Posts response to Band room

Prerequisites:
    1. Set environment variables:
       - BAND_WS_URL: WebSocket URL
       - BAND_REST_URL: REST API URL
       - ACP_AGENT_COMMAND: Command to spawn the ACP agent
         (default: "npx @zed-industries/codex-acp")

    2. Have the remote ACP agent installed and available in PATH

Run with:
    uv run examples/acp/clients/generic.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import ACPClientAdapter
from band.config import load_agent_config

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("acp_client_agent")

    # Command to spawn the remote ACP agent
    acp_command = shlex.split(
        os.getenv("ACP_AGENT_COMMAND", "npx @zed-industries/codex-acp")
    )

    # Working directory for ACP sessions
    acp_cwd = os.getenv("ACP_AGENT_CWD", ".")

    # Create adapter pointing to remote ACP agent
    adapter = ACPClientAdapter(
        command=acp_command,
        cwd=acp_cwd,
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info(
        "Starting ACP client bridge (forwarding to '%s')...",
        " ".join(acp_command),
    )
    logger.info("Messages from Band will be forwarded to the ACP agent.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
