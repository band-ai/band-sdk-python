# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]", "anthropic>=0.75.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Jerry the mouse agent — outsmarts Tom!

This example shows an Agno-backed character agent with a custom personality.
Jerry uses the Band toolset to stay one step ahead of Tom, taunting him and
dodging his schemes.

Run Tom and Jerry as two separate processes (each its own Band agent, here
backed by Agno) to show that they communicate through the room regardless of
which adapter backs them — pair this with any other adapter's Tom and the
conversation works just the same. Start each in its own terminal:

    uv run examples/agno/03_tom_agent.py
    uv run examples/agno/04_jerry_agent.py

The character prompt is loaded from a shared prompts module reused across
adapter implementations.

Requires:
    - agent_config.yaml with a `jerry_agent` entry (agent_id + api_key)
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

from prompts.characters import generate_jerry_prompt


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
        instructions=generate_jerry_prompt("Jerry"),
    )

    agent = Agent.from_config(
        "jerry_agent",
        # emit=EXECUTION posts tool_call/tool_result events so Jerry's platform
        # actions (lookup, invite, send) are visible in the room.
        adapter=AgnoAdapter(
            agno_agent, features=AdapterFeatures(emit={Emit.EXECUTION})
        ),
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Jerry is ready to outsmart Tom...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
