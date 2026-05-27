# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[letta]", "python-dotenv"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Letta agent example.

Connects a Letta agent to the Thenvoi platform using MCP tools for
bidirectional communication.  Works with both Letta Cloud and self-hosted
Letta servers.

Environment variables:
    THENVOI_WS_URL      Thenvoi WebSocket URL (required)
    THENVOI_REST_URL    Thenvoi REST URL (required)
    LETTA_BASE_URL      Letta server URL (default: https://api.letta.com)
                        Set to http://localhost:8283 for self-hosted.
    LETTA_API_KEY       Letta API key (required for Cloud, optional for self-hosted)
    LETTA_PROJECT       Letta Cloud project name (optional)
    LETTA_MODEL         LLM model ID (default: openai/gpt-5.4-mini)
    MCP_SERVER_URL      thenvoi-mcp server URL (default: http://localhost:8002/sse)
                        Must be reachable by the Letta server. For Letta Cloud
                        this must be a publicly reachable URL (e.g. via ngrok).

Letta Cloud usage:
    export LETTA_API_KEY="your-letta-cloud-api-key"
    # MCP server must be publicly reachable for Letta Cloud to call it
    export MCP_SERVER_URL="https://your-mcp-server.example.com/sse"
    uv run examples/letta/01_basic_agent.py

Self-hosted usage:
    export LETTA_BASE_URL="http://localhost:8283"
    # No LETTA_API_KEY needed; localhost MCP works since both run locally
    docker run -p 8283:8283 letta/letta:latest
    uv run examples/letta/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging

from thenvoi import Agent
from thenvoi.adapters.letta import LettaAdapter, LettaAdapterConfig

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")
    # Create adapter — defaults to Letta Cloud (https://api.letta.com).
    # For self-hosted, set LETTA_BASE_URL=http://localhost:8283
    adapter = LettaAdapter(
        config=LettaAdapterConfig(
            # Letta Cloud by default; override with LETTA_BASE_URL for self-hosted
            base_url=os.getenv("LETTA_BASE_URL", "https://api.letta.com"),
            # Required for Letta Cloud, optional for self-hosted
            api_key=os.getenv("LETTA_API_KEY"),
            # Letta Cloud project scoping (optional)
            project=os.getenv("LETTA_PROJECT"),
            # LLM model to use
            model=os.getenv("LETTA_MODEL", "openai/gpt-5.4-mini"),
            # thenvoi-mcp server for platform tool execution
            mcp_server_url=os.getenv("MCP_SERVER_URL", "http://localhost:8002/sse"),
            # Custom prompt section
            custom_section="You are a helpful assistant. Be concise and friendly.",
        ),
    )

    # Create and start agent
    agent = Agent.from_config(
        "letta_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Letta agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
