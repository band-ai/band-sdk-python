# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[crewai]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
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
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import CrewAIAdapter
from thenvoi.core.types import AdapterFeatures, Emit

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
        features=AdapterFeatures(emit={Emit.EXECUTION}),
        verbose=True,
    )

    # Create and start agent
    agent = Agent.from_config(
        "crewai_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting CrewAI research agent...")
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
