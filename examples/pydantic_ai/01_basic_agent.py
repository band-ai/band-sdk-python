# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[pydantic-ai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Pydantic AI agent example.

This is the simplest way to create a Band agent with Pydantic AI.
The adapter handles tool registration automatically.

Run with:
    uv run examples/pydantic_ai/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import PydanticAIAdapter

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
    # Create adapter with framework-specific settings
    adapter = PydanticAIAdapter(
        model="openai:gpt-5.4-mini",
        custom_section="You are a helpful assistant. Be concise and friendly.",
    )

    # Create and start agent
    agent = Agent.from_config(
        "pydantic_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Pydantic AI agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
