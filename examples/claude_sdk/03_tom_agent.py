# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[claude_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Tom the cat agent using Claude SDK.

This example shows how to create a character agent with a custom personality
using the Claude Agent SDK. Tom uses platform tools to find and invite Jerry,
then tries various tactics to lure Jerry out of his mouse hole.

Prerequisites:
    - Node.js 20+ installed
    - Claude Code CLI: npm install -g @anthropic-ai/claude-code
    - Add tom_agent credentials to agent_config.yaml
    - Jerry agent should be online for full interaction

Run with (from repo root):
    uv run examples/claude_sdk/03_tom_agent.py

Note: Must be run from repo as it imports prompts/characters.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt
from band import Agent, configure_logging
from band.adapters import ClaudeSDKAdapter
from band.core.types import Emit

configure_logging(
    logging.INFO,
    extra_loggers={
        "band_claude_sdk_agent": logging.INFO,
        "session_manager": logging.INFO,
    },
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Run Tom the cat agent."""
    load_dotenv()

    adapter = ClaudeSDKAdapter(
        custom_section=generate_tom_prompt("Tom"),
        emit=Emit.TOOL_CALLS | Emit.THOUGHTS,
    )

    agent = Agent.from_config(
        "tom_agent",
        adapter=adapter,
    )

    logger.info("Tom is on the prowl, looking for Jerry...")
    logger.info("Agent ID: %s", agent.runtime.agent_id)
    logger.info("Press Ctrl+C to stop")

    try:
        async with agent:
            await agent.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
