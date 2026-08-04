# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant,logging]"]
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

    # Adapter owns the Parlant server (fresh ports each run, so Tom and Jerry
    # can run side by side). Band tools attach to the guideline by default.
    adapter = ParlantAdapter(
        name="Jerry",
        description=generate_jerry_prompt("Jerry"),
        nlp_service=p.NLPServices.openai,
    )
    adapter.add_guideline(
        condition="User sends a message or asks something",
        action="Respond using band_send_message with the user's name in mentions. Stay in character as Jerry the mouse.",
    )

    # Create and start agent
    agent = Agent.from_config(
        "jerry_agent",
        adapter=adapter,
    )

    logger.info("Jerry is cozy in his hole, watching for Tom...")
    async with agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
