# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[anthropic]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Tom the cat agent - tries to catch Jerry!

This example shows how to create a character agent with a custom personality.
Tom uses platform tools to find and invite Jerry, then tries various tactics
to lure Jerry out of his mouse hole.

The character prompt is loaded from a shared prompts module that can be
reused across different adapter implementations.

Run with (from repo root):
    uv run examples/anthropic/03_tom_agent.py

Note: Must be run from repo as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add parent directory to path for prompts import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt

from setup_logging import setup_logging
from band import Agent
from band.adapters import AnthropicAdapter

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

    # Load Tom's credentials from agent_config.yaml
    # Create adapter with Tom's character prompt
    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt=generate_tom_prompt("Tom"),
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
