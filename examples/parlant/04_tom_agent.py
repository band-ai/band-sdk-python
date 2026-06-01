# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[parlant]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Tom the cat agent using Parlant.

This example shows how to create a character agent with a custom personality
using Parlant. Tom tries various tactics to lure Jerry out of his mouse hole.

Run with (from repo root):
    uv run examples/parlant/04_tom_agent.py

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

from prompts.characters import generate_tom_prompt
from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import ParlantAdapter

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

    # Load Tom's credentials from agent_config.yaml
    async with p.Server(nlp_service=p.NLPServices.openai) as server:
        # Create Parlant agent with Tom's personality
        parlant_agent = await server.create_agent(
            name="Tom",
            description=generate_tom_prompt("Tom"),
        )

        await parlant_agent.create_guideline(
            condition="User sends a message or asks something",
            action="Stay in character as Tom the Cat.",
        )

        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
        )

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
