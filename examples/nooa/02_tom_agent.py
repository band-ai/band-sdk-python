# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["band-sdk[nooa]>=5.1.0"]
# ///
"""
Tom the cat agent - tries to catch Jerry!

A character agent driven by NOOA. Tom uses the Band tools on `self.room` to find
and invite Jerry, then tries to lure him out of his mouse hole. Run it next to
03_jerry_agent.py to watch two NOOA agents talk to each other.

The character prompt comes from the shared prompts module, so the same persona
runs across every adapter. The model writes and runs Python inside this
process, so run it in a container or VM.

Run with (from the repo root):
    uv run --extra nooa python examples/nooa/02_tom_agent.py   # this checkout
    uv run examples/nooa/02_tom_agent.py                       # released band-sdk >= 5.1.0

Note: Must be run from the repo as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from nooa.unifiedllm.registry import get_llm_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt

from band import Agent, configure_logging
from band.adapters import NooaAdapter, NooaAdapterConfig, NooaRoomAgent

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


class Tom(NooaRoomAgent, llm=get_llm_client("anthropic/claude-sonnet-5-5")):
    """You are Tom, a cartoon cat in a Band chat room."""


async def main() -> None:
    load_dotenv()

    adapter = NooaAdapter(
        NooaAdapterConfig(custom_section=generate_tom_prompt("Tom")),
        agent=Tom,
    )

    logger.info("Tom is on the prowl, looking for Jerry...")
    async with Agent.from_config("tom_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
