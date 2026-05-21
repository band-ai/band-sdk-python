# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[google_adk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Google ADK agent example.

This is the simplest way to create a Band agent using the Google Agent
Development Kit (ADK) with Gemini models. The adapter handles conversation
history, tool calling, and platform integration via ADK's built-in Runner.

Requires GOOGLE_API_KEY (or GOOGLE_GENAI_API_KEY) environment variable for
Gemini authentication, in addition to the Band credentials.

Run with:
    uv run examples/google_adk/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging
from band import Agent
from band.adapters import GoogleADKAdapter
from band.config import load_agent_config

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("google_adk_agent")

    # Create adapter with Google ADK settings
    adapter = GoogleADKAdapter(
        model="gemini-2.5-flash",
        custom_section="You are a helpful assistant. Be concise and friendly.",
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Google ADK agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
