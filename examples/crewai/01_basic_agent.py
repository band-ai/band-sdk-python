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

from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters import CrewAIAdapter

configure_logging(logging.INFO, extra_loggers={"band_crewai_agent": logging.INFO})
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    # Create adapter with framework-specific settings
    adapter = CrewAIAdapter(
        model="gpt-5.4-mini",
        custom_section="You are a helpful assistant. Be concise and friendly.",
    )

    logger.info("Starting CrewAI agent...")
    async with Agent.from_config(
        "crewai_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
