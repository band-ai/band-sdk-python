# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
GitHub Copilot CLI ACP Client - Use GitHub Copilot from Band.

Spawns the GitHub Copilot CLI's ACP server (`copilot --acp`) as a subprocess and
bridges it to the Band platform. Messages from Band rooms are forwarded to
Copilot, and Copilot's responses (streaming text, thoughts, tool calls) are
posted back to the room. Band tools are injected through a local, localhost-only
MCP server (HTTP/SSE) that Copilot calls over ACP.

Copilot speaks vanilla ACP (no `copilot/*` extension methods), so no custom
client profile is needed.

Architecture:
    Band Platform (message arrives in room)
      -> CopilotACPAdapter
        -> `copilot --acp` subprocess
          -> Copilot CLI (with Band MCP tools injected)
            -> session_update responses streamed back
        -> Posts response to Band room

Prerequisites:
    1. GitHub Copilot CLI installed and on PATH:
       https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli

    2. A Copilot-entitled GitHub token in the environment (Copilot reads
       GITHUB_TOKEN / GH_TOKEN / COPILOT_GITHUB_TOKEN automatically):
       export GITHUB_TOKEN=...

    3. Set environment variables:
       - BAND_API_KEY: Your Band API key (required for tool injection)

    4. Optionally configure:
       - ACP_AGENT_CWD: Working directory for Copilot sessions (default: .)
       - COPILOT_ACP_HOST / COPILOT_ACP_PORT: connect to an already-running
         `copilot --acp --port <PORT>` over TCP instead of spawning a subprocess
         (e.g. Copilot in a container). See examples/acp/copilot_docker/ .

Run with:
    uv run examples/acp/clients/copilot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import CopilotACPAdapter, CopilotACPAdapterConfig

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")
    rest_url = os.getenv("BAND_REST_URL", "https://app.band.ai")
    cwd = os.getenv("ACP_AGENT_CWD", ".")
    github_token = os.getenv("GITHUB_TOKEN")

    # Optional TCP transport: connect to an already-running `copilot --acp --port`
    # instead of spawning a local subprocess.
    host = os.getenv("COPILOT_ACP_HOST")
    port = os.getenv("COPILOT_ACP_PORT")

    config = CopilotACPAdapterConfig(
        host=host,
        port=int(port) if port else None,
        cwd=cwd,
        github_token=github_token,
        rest_url=rest_url,
        inject_band_tools=True,
    )
    adapter = CopilotACPAdapter(config)

    agent = Agent.from_config(
        "copilot_acp_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting GitHub Copilot ACP client bridge...")
    logger.info("Messages from Band will be forwarded to Copilot.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
