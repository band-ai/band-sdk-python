# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[strands]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Tom the cat agent - tries to catch Jerry!

A character agent driven by Strands. Tom uses the Band platform tools to find
and invite Jerry, then tries to lure him out of his mouse hole. Run it next to
07_jerry_agent.py to watch two Strands agents talk to each other.

The character prompt comes from the shared prompts module, so the same persona
runs across every adapter.

Run with (from repo root):
    uv run examples/strands/06_tom_agent.py

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

from prompts.characters import generate_tom_prompt

from setup_logging import setup_logging
from band import Agent
from band.adapters import StrandsAdapter

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    adapter = StrandsAdapter(
        model=OpenAIModel(model_id="gpt-5.4-mini"),
        custom_section=generate_tom_prompt("Tom"),
    )

    agent = Agent.from_config(
        "tom_agent",
        adapter=adapter,
    )

    logger.info("Tom is on the prowl, looking for Jerry...")
    async with agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
