# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[a2a]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic A2A adapter example.

This example connects to a remote A2A-compliant agent and makes it available
as a Band platform agent. Messages from the platform are forwarded to the
A2A agent, and responses are posted back to the chat.

Features:
    - Automatic session state persistence via task events
    - Session rehydration when agent rejoins a room (context_id restored)
    - Task resumption for input_required state via A2A resubscribe

Prerequisites:
    1. Start an A2A-compliant agent (e.g., the LangGraph currency agent):

       cd /path/to/a2a-samples/samples/python/agents/langgraph
       export GOOGLE_API_KEY=xxx  # or OPENAI_API_KEY for OpenAI
       python -m app --host localhost --port 10000

    2. Verify the agent is running:
       curl http://localhost:10000/.well-known/agent.json

Run with:
    uv run examples/a2a_bridge/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import A2AAdapter

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
    # URL of the remote A2A agent
    # Default: LangGraph currency agent sample running locally
    a2a_url = os.getenv("A2A_AGENT_URL", "http://localhost:10000")

    # Create adapter pointing to remote A2A agent
    adapter = A2AAdapter(
        remote_url=a2a_url,
        streaming=True,  # Enable SSE streaming for real-time updates
    )

    # Create and start agent
    agent = Agent.from_config(
        "a2a_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting A2A bridge agent (forwarding to %s)...", a2a_url)
    logger.info("Try asking: 'What is 10 USD in EUR?'")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
