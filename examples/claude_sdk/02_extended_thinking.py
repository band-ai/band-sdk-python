#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[claude_sdk]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Extended Thinking Claude SDK Agent Example.

This example shows how to create an agent with extended thinking enabled,
which allows Claude to reason through complex problems step-by-step.

Extended thinking is useful for:
- Complex problem solving
- Multi-step reasoning
- Code analysis and debugging
- Planning and decision making

Prerequisites:
    1. Node.js 20+ installed
    2. Claude Code CLI: npm install -g @anthropic-ai/claude-code
    3. Add claude_sdk_agent credentials to agent_config.yaml
    4. Set environment variables in .env:
       - THENVOI_WS_URL
       - THENVOI_REST_URL
       - ANTHROPIC_API_KEY

Run with:
    uv run examples/claude_sdk/02_extended_thinking.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add examples directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import ClaudeSDKAdapter
from thenvoi.config import load_agent_config
from thenvoi.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the extended thinking Claude SDK agent."""
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    # Load credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("claude_sdk_agent")

    # Create adapter with extended thinking enabled.  `model="opus"` is a
    # family alias resolved by the npm `claude` binary at runtime — always
    # the latest Opus on the installed CLI, no version pinning.
    adapter = ClaudeSDKAdapter(
        model="opus",
        fallback_model="sonnet",
        custom_section="""You are a thoughtful AI assistant that excels at
complex problem-solving. When faced with challenging questions:
1. Break down the problem into smaller parts
2. Consider multiple approaches
3. Evaluate trade-offs
4. Provide clear, well-reasoned answers""",
        max_thinking_tokens=10000,  # Enable extended thinking
        features=AdapterFeatures(
            emit={Emit.EXECUTION, Emit.THOUGHTS}
        ),  # Report execution and thinking as events
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Claude SDK agent with extended thinking...")
    logger.info("Agent ID: %s", agent_id)
    logger.info("Max thinking tokens: 10000")
    logger.info("Press Ctrl+C to stop")

    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
