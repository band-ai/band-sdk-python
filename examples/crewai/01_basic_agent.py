# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic CrewAI agent example.

This is the simplest way to create a Band agent using the CrewAI framework.
The adapter handles conversation history, tool calling, and platform integration.

CrewAI (https://docs.crewai.com/) provides:
- Agent collaboration with defined roles and goals
- Task orchestration with processes
- Memory and knowledge management

Run with:
    uv run examples/crewai/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import CrewAIAdapter

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
    adapter = CrewAIAdapter(
        model="gpt-5.4-mini",
        custom_section="You are a helpful assistant. Be concise and friendly.",
    )

    # Create and start agent
    agent = Agent.from_config(
        "crewai_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting CrewAI agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
