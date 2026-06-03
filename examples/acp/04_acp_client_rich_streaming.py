# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
ACP Client with rich streaming - Thoughts, tool calls, and plans.

This example shows how the ACPClientAdapter handles rich session_update
chunks from remote ACP agents. Beyond plain text, it captures:

  - Thoughts: Internal reasoning from the agent
  - Tool calls: Tool invocations with name, args, and results
  - Plans: Task plans with status tracking

All rich events are posted back to the Band platform with full type
fidelity, so other participants can see exactly what the remote agent
is doing.

Permission requests from the ACP agent are also posted to the platform
as visible events (auto-allowed by default).

Architecture:
    Band Platform (message arrives in room)
      -> ACPClientAdapter.on_message()
        -> remote ACP prompt/session handling
          -> Remote ACP Agent (e.g., Claude Code)
            -> session_update: thought -> tools.send_event("thought")
            -> session_update: tool_call -> tools.send_event("tool_call")
            -> session_update: text -> tools.send_message()
            -> request_permission -> tools.send_event("tool_call", permission)
        -> All events visible on Band platform

Prerequisites:
    1. Set environment variables:
       - BAND_WS_URL: WebSocket URL
       - BAND_REST_URL: REST API URL
       - ACP_AGENT_COMMAND: Command to spawn
         (default: "npx @zed-industries/codex-acp")

    2. Have the remote ACP agent installed and available in PATH

Run with:
    uv run examples/acp/04_acp_client_rich_streaming.py
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

setup_logging(logging.DEBUG)
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

    logger.info("Starting ACP client bridge with rich streaming...")
    logger.info("Command: %s", " ".join(acp_command))
    logger.info("Thoughts, tool calls, and plans will be posted to the platform.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
