# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[claude_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/band-sdk-python.git" }
# ///
"""
Jerry the mouse agent using Claude SDK.

This example shows how to create a character agent with a custom personality
using the Claude Agent SDK. Jerry is a clever mouse who lives in his hole
and teases Tom the cat while staying safe from being caught.

Prerequisites:
    - Node.js 20+ installed
    - Claude Code CLI: npm install -g @anthropic-ai/claude-code
    - Add jerry_agent credentials to agent_config.yaml
    - Tom agent should be online for full interaction

Run with (from repo root):
    uv run examples/claude_sdk/04_jerry_agent.py

Note: Must be run from repo as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_jerry_prompt
from setup_logging import setup_logging
from band import Agent
from band.adapters import ClaudeSDKAdapter
from thenvoi.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    """Run Jerry the mouse agent."""
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    adapter = ClaudeSDKAdapter(
        custom_section=generate_jerry_prompt("Jerry"),
        features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}),
    )

    agent = Agent.from_config(
        "jerry_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Jerry is cozy in his hole, watching for Tom...")
    logger.info("Agent ID: %s", agent.runtime.agent_id)
    logger.info("Press Ctrl+C to stop")

    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
