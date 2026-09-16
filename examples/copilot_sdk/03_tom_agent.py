#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[copilot_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Tom the cat agent - tries to catch Jerry!

This example shows how to create a character agent with a custom personality
using the Copilot SDK adapter.

The character prompt is loaded from a shared prompts module that can be
reused across different adapter implementations.

Run with (from repo root):
    uv run examples/copilot_sdk/03_tom_agent.py

Note: Run from the repo root so agent_config.yaml and .env resolve
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Add parent directory to path for prompts import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_tom_prompt

from band import Agent, configure_logging
from band.adapters import CopilotSDKAdapter, CopilotSDKAdapterConfig

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    github_token: str = ""


async def main() -> None:
    load_dotenv()
    settings = Settings()

    adapter = CopilotSDKAdapter(
        CopilotSDKAdapterConfig(
            custom_section=generate_tom_prompt("Tom"),
            github_token=settings.github_token or None,
        ),
    )

    logger.info("Tom is on the prowl, looking for Jerry...")
    async with Agent.from_config(
        "tom_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
