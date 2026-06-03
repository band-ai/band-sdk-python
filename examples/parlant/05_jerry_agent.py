# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Jerry the mouse agent using Parlant.

This example shows how to create a character agent with a custom personality
using Parlant. Jerry is a clever mouse who lives in his hole
and teases Tom the cat while staying safe from being caught.

Run with (from repo root):
    uv run examples/parlant/05_jerry_agent.py

Note: Must be run from repo as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import parlant.sdk as p
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_jerry_prompt
from setup_logging import setup_logging
from band import Agent
from band.adapters import ParlantAdapter

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

    # Load Jerry's credentials from agent_config.yaml
    async with p.Server(nlp_service=p.NLPServices.openai) as server:
        # Create Parlant agent with Jerry's personality
        parlant_agent = await server.create_agent(
            name="Jerry",
            description=generate_jerry_prompt("Jerry"),
        )

        await parlant_agent.create_guideline(
            condition="User sends a message or asks something",
            action="Stay in character as Jerry the Mouse.",
        )

        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
        )

        agent = Agent.from_config(
            "jerry_agent",
            adapter=adapter,
            ws_url=ws_url,
            rest_url=rest_url,
        )

        logger.info("Jerry is cozy in his hole, watching for Tom...")
        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
