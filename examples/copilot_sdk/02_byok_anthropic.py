#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[copilot_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Copilot SDK Agent with BYOK (bring your own key) — Anthropic provider.

Runs the Copilot runtime while doing model inference against your own
Anthropic API key instead of the Copilot subscription. BYOK does not require
GitHub authentication; the Anthropic key pays for the tokens.

Prerequisites:
    1. Add copilot_sdk_agent credentials to agent_config.yaml
    2. Set environment variables in .env:
       - BAND_WS_URL
       - BAND_REST_URL
       - ANTHROPIC_API_KEY (BYOK inference)

Run with:
    uv run examples/copilot_sdk/02_byok_anthropic.py
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

from copilot import ProviderConfig

from band import Agent, configure_logging
from band.adapters import CopilotSDKAdapter, CopilotSDKAdapterConfig
from band.core.types import Emit

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    anthropic_api_key: str


async def main() -> None:
    """Run a Copilot SDK agent with Anthropic BYOK inference."""
    load_dotenv()
    settings = Settings()

    # With BYOK the `model` names the provider's model, not a Copilot one.
    adapter = CopilotSDKAdapter(
        CopilotSDKAdapterConfig(
            model="claude-haiku-4-5",
            provider=ProviderConfig(
                type="anthropic",
                # base_url is required by the runtime, even for known providers.
                base_url="https://api.anthropic.com",
                api_key=settings.anthropic_api_key,
            ),
            custom_section="You are a helpful assistant. Be concise and friendly.",
            use_logged_in_user=False,
            # Pin a unique per-example session prefix.
            session_id_prefix="band-copilot-byok-",
        ),
        emit=Emit.TOOL_CALLS,
    )

    agent = Agent.from_config(
        "copilot_sdk_agent",
        adapter=adapter,
    )

    logger.info("Starting Copilot SDK agent with Anthropic BYOK...")
    logger.info("Agent ID: %s", agent.runtime.agent_id)
    logger.info("Press Ctrl+C to stop")

    try:
        async with agent:
            await agent.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
