# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
CrewAI coordinator agent for multi-agent orchestration.

Demonstrates a coordinator agent that can bring in other agents
and orchestrate multi-agent collaboration on the Band platform.

This is similar to CrewAI's hierarchical process where a manager
delegates tasks to specialized agents.

Run with:
    uv run examples/crewai/03_coordinator_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import CrewAIAdapter
from band.core.types import AdapterFeatures, Emit

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
    # Create a coordinator agent that orchestrates other agents
    adapter = CrewAIAdapter(
        model="gpt-5.4-mini",
        role="Team Coordinator",
        goal="Orchestrate collaboration between specialized agents to accomplish complex tasks",
        backstory="""You are an experienced project coordinator who excels at
        breaking down complex problems into manageable tasks and delegating them
        to the right specialists. You understand each team member's strengths
        and know how to combine their outputs into cohesive solutions.

        You have access to tools that let you:
        - Look up available agents (band_lookup_peers)
        - Add agents to the conversation (band_add_participant)
        - Remove agents when they're no longer needed (band_remove_participant)
        - Create new chat rooms for focused discussions (band_create_chatroom)

        Use these tools to build the right team for each user request.""",
        custom_section="""
When coordinating:
1. First understand what the user needs
2. Identify which specialists would be helpful
3. Use band_lookup_peers to find available agents
4. Add relevant agents with band_add_participant
5. Direct the conversation by mentioning specific agents
6. Synthesize outputs from multiple agents
7. Clean up by removing agents no longer needed
""",
        features=AdapterFeatures(emit={Emit.EXECUTION}),
        verbose=True,
    )

    # Create and start agent
    agent = Agent.from_config(
        "coordinator_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting CrewAI coordinator agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
