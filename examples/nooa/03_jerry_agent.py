# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["band-sdk[nooa]>=4.0.0"]
# ///
"""
Jerry the mouse agent - outsmarts Tom!

The counterpart to 02_tom_agent.py: Jerry taunts Tom from his mouse hole while
staying safe. Run both to see two NOOA-backed peers hold a conversation in a
Band room.

The character prompt comes from the shared prompts module, so the same persona
runs across every adapter. The model writes and runs Python inside this
process, so run it in a container or VM.

Run with (from repo root):
    uv run examples/nooa/03_jerry_agent.py

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

from prompts.characters import generate_jerry_prompt

from band import Agent, configure_logging
from band.adapters import NooaAdapter, NooaAdapterConfig, NooaRoomAgent

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


class Jerry(NooaRoomAgent, llm=get_llm_client("anthropic/claude-sonnet-5-5")):
    """You are Jerry, a cartoon mouse in a Band chat room."""


async def main() -> None:
    load_dotenv()

    adapter = NooaAdapter(
        NooaAdapterConfig(custom_section=generate_jerry_prompt("Jerry")),
        agent=Jerry,
    )

    logger.info("Jerry is watching for Tom from his mouse hole...")
    async with Agent.from_config("jerry_agent", adapter=adapter) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
