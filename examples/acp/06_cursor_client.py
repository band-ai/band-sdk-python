# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[acp]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Cursor ACP Client - Use Cursor's AI agent from Thenvoi.

Spawns Cursor's CLI agent (`cursor agent acp`) as a subprocess and bridges it
to the Thenvoi platform. Messages from Thenvoi rooms are forwarded to Cursor,
and Cursor's responses (including tool calls, plans, and streaming text) are
posted back to the room.

Note: Cursor IDE does NOT yet support connecting to external ACP agents (i.e.,
you cannot add Thenvoi as an agent inside Cursor's UI). This integration works
the other direction — Thenvoi spawns Cursor's agent as a backend.

For the reverse direction (IDE connects to Thenvoi), see:
- JetBrains: examples/acp/07_jetbrains_server.py
- Zed: examples/acp/01_basic_acp_server.py
- Any ACP client: thenvoi-acp CLI

Architecture:
    Thenvoi Platform (message arrives in room)
      -> ACPClientAdapter
        -> Cursor ACP subprocess
          -> Cursor CLI Agent (with Thenvoi MCP tools injected)
            -> session_update responses streamed back
        -> Posts response to Thenvoi room

Prerequisites:
    1. Cursor CLI installed and authenticated:
       cursor agent login
       # OR set CURSOR_API_KEY / CURSOR_AUTH_TOKEN environment variable

    2. Set environment variables:
       - THENVOI_API_KEY: Your Thenvoi API key (required for tool injection)

    3. Optionally configure:
       - CURSOR_API_KEY: Cursor API key (alternative to `cursor agent login`)
       - ACP_AGENT_CWD: Working directory for Cursor sessions (default: .)

Run with:
    uv run examples/acp/06_cursor_client.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import ACPClientAdapter
from thenvoi.config import load_agent_config
from thenvoi.integrations.acp.client_profiles import CursorACPClientProfile

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv(
        "THENVOI_WS_URL", "wss://app.thenvoi.com/api/v1/socket/websocket"
    )
    rest_url = os.getenv("THENVOI_REST_URL", "https://app.thenvoi.com")

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("cursor_agent")

    # Working directory for Cursor sessions
    cwd = os.getenv("ACP_AGENT_CWD", ".")

    # Cursor authentication environment — passed to the subprocess
    cursor_env: dict[str, str] = {}
    cursor_api_key = os.getenv("CURSOR_API_KEY")
    cursor_auth_token = os.getenv("CURSOR_AUTH_TOKEN")
    if cursor_api_key:
        cursor_env["CURSOR_API_KEY"] = cursor_api_key
    if cursor_auth_token:
        cursor_env["CURSOR_AUTH_TOKEN"] = cursor_auth_token

    # Create adapter that spawns Cursor's ACP agent.
    # - auth_method="cursor_login" authenticates using your Cursor login
    # - Thenvoi tools are injected through a local localhost-only MCP server
    adapter = ACPClientAdapter(
        command=[os.path.expanduser("~/.local/bin/agent"), "acp"],
        cwd=cwd,
        env=cursor_env or None,
        rest_url=rest_url,
        inject_thenvoi_tools=True,
        auth_method="cursor_login",
        profile=CursorACPClientProfile(),
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Cursor ACP client bridge...")
    logger.info("Messages from Thenvoi will be forwarded to Cursor's agent.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
