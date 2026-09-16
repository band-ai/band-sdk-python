# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[strands]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Jerry the mouse agent - outsmarts Tom!

The counterpart to 06_tom_agent.py: Jerry taunts Tom from his mouse hole while
staying safe. Run both to see two Strands-backed peers hold a conversation in a
Band room.

The character prompt comes from the shared prompts module, so the same persona
runs across every adapter.

Run with (from repo root):
    uv run examples/strands/07_jerry_agent.py

Note: Must be run from the repo as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from strands.models.openai import OpenAIModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_jerry_prompt

from band import Agent, configure_logging
from band.adapters import StrandsAdapter

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    adapter = StrandsAdapter(
        model=OpenAIModel(model_id="gpt-5.4-mini"),
        custom_section=generate_jerry_prompt("Jerry"),
    )

    logger.info("Jerry is watching for Tom from his mouse hole...")
    async with Agent.from_config(
        "jerry_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
