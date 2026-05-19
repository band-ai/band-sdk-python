# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[pydantic-ai]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Agent with custom system prompt instructions.

Shows how to provide detailed custom instructions to shape agent behavior.

Run with:
    uv run examples/pydantic_ai/02_custom_instructions.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import PydanticAIAdapter

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
    # Create adapter with custom instructions
    adapter = PydanticAIAdapter(
        model="anthropic:claude-3-5-sonnet-latest",
        custom_section=CUSTOM_PROMPT,
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
