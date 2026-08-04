#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[claude_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Claude SDK Agent Example.

This example shows how to create a simple agent using the Claude Agent SDK
connected to the Band platform.

Prerequisites:
    1. Node.js 20+ installed
    2. Claude Code CLI: npm install -g @anthropic-ai/claude-code
    3. Add claude_sdk_agent credentials to agent_config.yaml
    4. Set environment variables in .env:
       - BAND_WS_URL
       - BAND_REST_URL
       - ANTHROPIC_API_KEY

Run with:
    uv run examples/claude_sdk/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add examples directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    """Run the basic Claude SDK agent."""
    load_dotenv()

    # Create adapter with Claude SDK settings.  Omitting `model` uses the
    # adapter's pinned default (the npm `claude` binary's auto-selection
    # fails under API-key auth); pass `model=` to override.
    adapter = ClaudeSDKAdapter(
        custom_section="You are a helpful assistant. Be concise and friendly.",
        emit=Emit.TOOL_CALLS | Emit.THOUGHTS,
    )

    agent = Agent.from_config(
        "claude_sdk_agent",
        adapter=adapter,
    )

    logger.info("Starting Claude SDK agent...")
    logger.info("Agent ID: %s", agent.runtime.agent_id)
    logger.info("Press Ctrl+C to stop")

    try:
        async with agent:
            await agent.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
