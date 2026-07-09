#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[copilot_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Tom the cat agent - tries to catch Jerry!

This example shows how to create a character agent with a custom personality
using the Copilot SDK adapter.

The character prompt is loaded from a shared prompts module that can be
reused across different adapter implementations.

Run with (from repo root):
    uv run examples/copilot_sdk/03_tom_agent.py

Note: Run from the repo root so agent_config.yaml and .env resolve
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
from band.adapters import CopilotSDKAdapter, CopilotSDKAdapterConfig

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

    adapter = CopilotSDKAdapter(
        CopilotSDKAdapterConfig(
            custom_section=generate_tom_prompt("Tom"),
            github_token=os.getenv("GITHUB_TOKEN"),
        ),
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
