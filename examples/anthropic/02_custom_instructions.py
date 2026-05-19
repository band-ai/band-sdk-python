# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[anthropic]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Agent with custom system prompt instructions.

Shows how to provide detailed custom instructions to shape agent behavior.
Also demonstrates execution reporting for visibility into tool calls.

Run with:
    uv run examples/anthropic/02_custom_instructions.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import AnthropicAdapter
from thenvoi.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)

CUSTOM_PROMPT = """
You are a technical support agent for a software company.

Guidelines:
- Be patient and thorough
- Ask clarifying questions before providing solutions
- Always verify the user's environment before troubleshooting
- Escalate to a human if you cannot resolve the issue

When helping users:
1. First acknowledge their issue
2. Ask for relevant details (OS, version, error messages)
3. Provide step-by-step solutions
4. Confirm the issue is resolved before closing
"""

async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")
    # Create adapter with custom instructions and execution reporting
    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt=CUSTOM_PROMPT,
        # Enable execution reporting to see tool calls in the chat
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    # Create and start agent
    agent = Agent.from_config(
        "support_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting support agent...")
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
