# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[anthropic]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Anthropic SDK agent example.

This is the simplest way to create a Thenvoi agent using the Anthropic Python SDK.
The adapter handles conversation history, tool calling, and platform integration.

Run with:
    uv run examples/anthropic/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import AnthropicAdapter

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
    # Create adapter with framework-specific settings
    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt="You are a helpful assistant. Be concise and friendly.",
    )

    # Create and start agent
    agent = Agent.from_config(
        "anthropic_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Anthropic agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
