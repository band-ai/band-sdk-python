# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]", "anthropic>=0.75.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Tom the cat agent — tries to catch Jerry!

This example shows an Agno-backed character agent with a custom personality.
Tom uses the Band toolset to find and invite Jerry, then tries various tactics
to lure Jerry out of his mouse hole.

Run Tom and Jerry as two separate processes (each its own Band agent, here
backed by Agno) to show that they communicate through the room regardless of
which adapter backs them — pair this with any other adapter's Jerry and the
conversation works just the same. Start each in its own terminal:

    uv run examples/agno/03_tom_agent.py
    uv run examples/agno/04_jerry_agent.py

The character prompt is loaded from a shared prompts module reused across
adapter implementations.

Requires:
    - agent_config.yaml with a `tom_agent` entry (agent_id + api_key)
    - BAND_WS_URL and BAND_REST_URL environment variables
    - ANTHROPIC_API_KEY environment variable (for the Claude model)

Note: Must be run from repo root as it imports prompts/characters.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from agno.agent import Agent as AgnoAgent
from agno.models.anthropic import Claude
from dotenv import load_dotenv

from band import Agent
from band.adapters import AgnoAdapter
from band.core.types import AdapterFeatures, Emit


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError("ANTHROPIC_API_KEY environment variable is required")

    ws_url = os.environ.get("BAND_WS_URL")
    rest_url = os.environ.get("BAND_REST_URL")
    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    # You own the Agno agent — model and in-character instructions.
    agno_agent = AgnoAgent(
        model=Claude(id="claude-sonnet-4-6"),
        instructions=generate_tom_prompt("Tom"),
    )

    agent = Agent.from_config(
        "tom_agent",
        # emit=EXECUTION posts tool_call/tool_result events so Tom's platform
        # actions (lookup, invite, send) are visible in the room.
        adapter=AgnoAdapter(
            agno_agent, features=AdapterFeatures(emit={Emit.EXECUTION})
        ),
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Tom is on the prowl, looking for Jerry...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
