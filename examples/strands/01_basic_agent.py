# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[strands]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Strands Agents example.

This is the simplest way to create a Band agent with AWS Strands Agents.
The adapter handles tool registration automatically.

Strands has no provider-prefix model string (a bare string means a Bedrock
model id), so the OpenAI provider is constructed explicitly; it reads
OPENAI_API_KEY from the environment.

Run with:
    uv run examples/strands/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from strands.models.openai import OpenAIModel

from setup_logging import setup_logging
from band import Agent
from band.adapters import StrandsAdapter

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

    # Create adapter with framework-specific settings
    adapter = StrandsAdapter(
        model=OpenAIModel(model_id="gpt-5.4-mini"),
        custom_section="You are a helpful assistant. Be concise and friendly.",
    )

    # Create and start agent
    agent = Agent.from_config(
        "strands_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Strands agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
