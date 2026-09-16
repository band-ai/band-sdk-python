# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
CrewAI agent with role, goal, and backstory.

Shows how to use CrewAI's agent definition pattern with role-based behavior.
This is the core concept from CrewAI - defining agents by their role and goals.

Run with:
    uv run examples/crewai/02_role_based_agent.py
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

    # Create adapter with CrewAI-style role definition
    adapter = CrewAIAdapter(
        model="gpt-5.4-mini",
        role="Research Assistant",
        goal="Help users find, analyze, and synthesize information efficiently",
        backstory="""You are an expert research assistant with years of experience
        in academic and business research. You excel at finding relevant information,
        analyzing data, and presenting findings in a clear, actionable format.
        You're known for your attention to detail and ability to connect disparate
        pieces of information into meaningful insights.""",
        verbose=True,
    )

    logger.info("Starting CrewAI research agent...")
    async with Agent.from_config(
        "crewai_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
