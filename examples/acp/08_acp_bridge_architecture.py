# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
ACP Bridge Architecture example.

This example demonstrates the refactored outbound ACP architecture where
Band bridge concerns are separated from generic ACP runtime plumbing.

Architecture:
    Band Platform (message arrives in room)
      -> ACPClientAdapter (Band bridge)
         - room/session mapping
         - bootstrap context + event emission
         - Band MCP tool policy (adapter-level)
      -> ACPRuntime (generic ACP subprocess/session plumbing)
      -> Remote ACP runtime (Codex, Claude Code, Gemini CLI, Cursor, etc.)

Relation to A2A:
    The analogy holds at the bridge boundary: both adapters map Band room
    messages to a remote protocol session and stream responses back.

    The main difference is transport ownership:
    - A2A adapter talks to a remote A2A peer over HTTP/SSE.
    - ACP outbound can spawn a local ACP subprocess and manage its lifecycle.

Prerequisites:
    1. Set BAND_API_KEY in your environment.
    2. Install an ACP-capable runtime (default command uses codex-acp).

Optional environment variables:
    - ACP_AGENT_COMMAND (default: "npx @zed-industries/codex-acp")
    - ACP_AGENT_CWD (default: ".")
    - ACP_AUTH_METHOD (example: "cursor_login")
    - ACP_INJECT_BAND_TOOLS (default: true)

Run with:
    uv run examples/acp/08_acp_bridge_architecture.py
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
from band.integrations.acp.client_profiles import CursorACPClientProfile

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")
    command = shlex.split(
        os.getenv("ACP_AGENT_COMMAND", "npx @zed-industries/codex-acp")
    )
    cwd = os.getenv("ACP_AGENT_CWD", ".")
    auth_method = os.getenv("ACP_AUTH_METHOD")
    inject_band_tools = os.getenv("ACP_INJECT_BAND_TOOLS", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    profile_name = os.getenv("ACP_CLIENT_PROFILE", "").strip().lower()
    profile = CursorACPClientProfile() if profile_name == "cursor" else None

    adapter = ACPClientAdapter(
        command=command,
        cwd=cwd,
        rest_url=rest_url,
        inject_band_tools=inject_band_tools,
        auth_method=auth_method,
        profile=profile,
    )

    agent = Agent.from_config(
        "acp_client_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting ACP bridge architecture example...")
    logger.info("ACP command: %s", " ".join(command))
    logger.info("Band tool injection enabled: %s", inject_band_tools)
    logger.info(
        "ACP client profile: %s",
        type(profile).__name__ if profile else "None",
    )

    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
