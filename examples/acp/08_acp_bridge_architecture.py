# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[acp]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
ACP Bridge Architecture example.

This example demonstrates the refactored outbound ACP architecture where
Thenvoi bridge concerns are separated from generic ACP runtime plumbing.

Architecture:
    Thenvoi Platform (message arrives in room)
      -> ACPClientAdapter (Thenvoi bridge)
         - room/session mapping
         - bootstrap context + event emission
         - Thenvoi MCP tool policy (adapter-level)
      -> ACPRuntime (generic ACP subprocess/session plumbing)
      -> External ACP runtime (Codex, Claude Code, Gemini CLI, Cursor, etc.)

Relation to A2A:
    The analogy holds at the bridge boundary: both adapters map Thenvoi room
    messages to an external protocol session and stream responses back.

    The main difference is transport ownership:
    - A2A adapter talks to a remote A2A peer over HTTP/SSE.
    - ACP outbound can spawn a local ACP subprocess and manage its lifecycle.

Prerequisites:
    1. Set THENVOI_API_KEY in your environment.
    2. Install an ACP-capable runtime (default command uses codex-acp).

Optional environment variables:
    - ACP_AGENT_COMMAND (default: "npx @zed-industries/codex-acp")
    - ACP_AGENT_CWD (default: ".")
    - ACP_AUTH_METHOD (example: "cursor_login")
    - ACP_INJECT_THENVOI_TOOLS (default: true)

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

    agent_id, api_key = load_agent_config("acp_client_agent")

    command = shlex.split(
        os.getenv("ACP_AGENT_COMMAND", "npx @zed-industries/codex-acp")
    )
    cwd = os.getenv("ACP_AGENT_CWD", ".")
    auth_method = os.getenv("ACP_AUTH_METHOD")
    inject_thenvoi_tools = os.getenv(
        "ACP_INJECT_THENVOI_TOOLS", "true"
    ).lower() not in {
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
        inject_thenvoi_tools=inject_thenvoi_tools,
        auth_method=auth_method,
        profile=profile,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting ACP bridge architecture example...")
    logger.info("ACP command: %s", " ".join(command))
    logger.info("Thenvoi tool injection enabled: %s", inject_thenvoi_tools)
    logger.info(
        "ACP client profile: %s",
        type(profile).__name__ if profile else "None",
    )

    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
