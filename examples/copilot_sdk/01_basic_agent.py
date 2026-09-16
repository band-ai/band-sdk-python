#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[copilot_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic GitHub Copilot SDK Agent Example.

This example shows how to create a simple agent using the GitHub Copilot SDK
connected to the Band platform. The SDK downloads and manages the Copilot CLI
runtime automatically on first use.

Prerequisites:
    1. GitHub Copilot access (token or `gh auth login` / `copilot` login)
    2. Add copilot_sdk_agent credentials to agent_config.yaml
    3. Set environment variables in .env:
       - BAND_WS_URL
       - BAND_REST_URL
       - GITHUB_TOKEN (optional — omit to use the logged-in GitHub user)

Run with:
    uv run examples/copilot_sdk/01_basic_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Add examples directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from band import Agent, configure_logging
from band.adapters import CopilotSDKAdapter, CopilotSDKAdapterConfig
from band.core.types import Emit

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    github_token: str = ""


async def main() -> None:
    """Run the basic Copilot SDK agent."""
    load_dotenv()
    settings = Settings()

    # Omitting `model` uses the Copilot CLI's default model; pass `model=`
    # to pin one. With no GITHUB_TOKEN the locally logged-in user is used.
    adapter = CopilotSDKAdapter(
        CopilotSDKAdapterConfig(
            custom_section="You are a helpful assistant. Be concise and friendly.",
            # Auth resolves automatically: GITHUB_TOKEN wins when set,
            # otherwise the logged-in GitHub user (gh auth login) is used.
            github_token=settings.github_token or None,
            # Pin a unique per-example session prefix.
            session_id_prefix="band-copilot-basic-",
        ),
        emit=Emit.TOOL_CALLS | Emit.THOUGHTS,
    )

    agent = Agent.from_config(
        "copilot_sdk_agent",
        adapter=adapter,
    )

    logger.info("Starting Copilot SDK agent...")
    logger.info("Agent ID: %s", agent.runtime.agent_id)
    logger.info("Press Ctrl+C to stop")

    try:
        async with agent:
            await agent.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
