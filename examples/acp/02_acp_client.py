# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[acp]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
ACP Client example - Use an external ACP agent from Thenvoi.

This example connects to an external ACP-compliant agent (Codex CLI, Gemini CLI,
Claude Code, Goose, etc.) and makes it available as a Thenvoi platform agent.
Messages from the platform are forwarded to the ACP agent, and responses are
posted back to the chat.

Architecture:
    Thenvoi Platform (message arrives in room)
      -> ACPClientAdapter
        -> external ACP agent subprocess/session
          -> External ACP Agent (Codex CLI, Gemini CLI, etc.)
            -> session_update responses streamed back
        -> Posts response to Thenvoi room

Prerequisites:
    1. Set environment variables:
       - THENVOI_WS_URL: WebSocket URL
       - THENVOI_REST_URL: REST API URL
       - ACP_AGENT_COMMAND: Command to spawn the ACP agent
         (default: "npx @zed-industries/codex-acp")

    2. Have the external ACP agent installed and available in PATH

Run with:
    uv run examples/acp/02_acp_client.py
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
from thenvoi import Agent
from thenvoi.adapters import ACPClientAdapter
from thenvoi.config import load_agent_config

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv(
        "THENVOI_WS_URL", "wss://app.thenvoi.com/api/v1/socket/websocket"
    )
    rest_url = os.getenv("THENVOI_REST_URL", "https://app.thenvoi.com")

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("acp_client_agent")

    # Command to spawn the external ACP agent
    acp_command = shlex.split(
        os.getenv("ACP_AGENT_COMMAND", "npx @zed-industries/codex-acp")
    )

    # Working directory for ACP sessions
    acp_cwd = os.getenv("ACP_AGENT_CWD", ".")

    # Create adapter pointing to external ACP agent
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
    logger.info("Messages from Thenvoi will be forwarded to the ACP agent.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
