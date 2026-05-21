# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[pydantic-ai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
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
from band.config import load_agent_config

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

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("pydantic_agent")

    # Create adapter with framework-specific settings
    adapter = PydanticAIAdapter(
        model="openai:gpt-4o",
        custom_section="You are a helpful assistant. Be concise and friendly.",
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Pydantic AI agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
