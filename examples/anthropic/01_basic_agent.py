# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[anthropic]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Anthropic SDK agent example.

This is the simplest way to create a Band agent using the Anthropic Python SDK.
The adapter handles conversation history, tool calling, and platform integration.

Run with:
    uv run examples/anthropic/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import AnthropicAdapter

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    # Create adapter with framework-specific settings
    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt="You are a helpful assistant. Be concise and friendly.",
    )

    # Create and start agent
    agent = Agent.from_config(
        "anthropic_agent",
        adapter=adapter,
    )

    logger.info("Starting Anthropic agent...")
    async with agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
