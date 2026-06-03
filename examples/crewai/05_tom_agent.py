# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Tom the cat agent using CrewAI.

This example shows how to create a character agent with a custom personality
using CrewAI. Tom uses platform tools to find and invite Jerry,
then tries various tactics to lure Jerry out of his mouse hole.

Run with (from repo root):
    uv run examples/crewai/05_tom_agent.py

Note: Must be run from repo as it imports prompts/characters.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt

from setup_logging import setup_logging
from band import Agent
from band.adapters import CrewAIAdapter

setup_logging()
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse optional character-name overrides for the example."""
    parser = argparse.ArgumentParser(description="Run the Tom CrewAI example agent")
    parser.add_argument(
        "--agent-name",
        default="Tom",
        help="Display name/persona to use for this agent in the prompt",
    )
    parser.add_argument(
        "--peer-name",
        default="Jerry",
        help="Display name of the Jerry agent to look up on Band",
    )
    return parser.parse_args()


async def main() -> None:
    load_dotenv()
    args = parse_args()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    # Load Tom's credentials from agent_config.yaml
    # Create adapter with Tom's character prompt
    adapter = CrewAIAdapter(
        model="gpt-5.4-mini",
        custom_section=generate_tom_prompt(args.agent_name, args.peer_name),
    )

    # Create and start agent
    agent = Agent.from_config(
        "tom_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Tom is on the prowl, looking for Jerry...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
