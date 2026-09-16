# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[anthropic]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
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

from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters import AnthropicAdapter

configure_logging(logging.INFO, extra_loggers={"band_anthropic_agent": logging.INFO})
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

    # Custom instructions; the adapter's default emit already shows tool
    # calls in the chat.
    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt=CUSTOM_PROMPT,
    )

    logger.info("Starting support agent...")
    async with Agent.from_config(
        "support_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
