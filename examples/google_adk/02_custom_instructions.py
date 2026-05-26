# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[google_adk]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Google ADK agent with custom instructions and model selection.

Demonstrates how to configure the Google ADK adapter with a custom system
prompt, model selection, and execution reporting.

Requires GOOGLE_API_KEY (or GOOGLE_GENAI_API_KEY) environment variable for
Gemini authentication, in addition to the Thenvoi credentials.

Run with:
    uv run examples/google_adk/02_custom_instructions.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import GoogleADKAdapter
from thenvoi.config import load_agent_config
from thenvoi.core.types import AdapterFeatures, Emit

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

    agent_id, api_key = load_agent_config("google_adk_agent")

    # Create adapter with custom configuration
    adapter = GoogleADKAdapter(
        model="gemini-2.5-flash",
        custom_section=(
            "You are a research assistant specializing in summarizing information. "
            "Always provide sources when possible and be thorough but concise."
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Google ADK agent with custom instructions...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
