# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[opencode]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""OpenCode agent with application-defined tools.

``additional_tools`` are served to OpenCode through a local MCP server beside
the standard Band platform tools. This is the right boundary for tools that
belong to an application rather than to the Band platform.

Try: "What is 18 percent of 240?"

Run with:
    uv run examples/opencode/03_custom_tools_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging
from settings import OpenCodeExampleSettings
from band import Agent
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)


class PercentageInput(BaseModel):
    """Calculate a percentage of a value."""

    percent: float = Field(description="Percentage to calculate, for example 18")
    value: float = Field(description="Value the percentage applies to")


def percentage(input: PercentageInput) -> str:
    """Return the calculated percentage."""
    return str(input.value * input.percent / 100)


async def main() -> None:
    settings = OpenCodeExampleSettings()
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=settings.opencode_base_url,
            provider_id=settings.opencode_provider_id,
            model_id=settings.opencode_model_id,
            agent=settings.opencode_agent,
            approval_mode=settings.opencode_approval_mode,
            custom_section=(
                "You are a helpful assistant. Use the percentage tool for percentage "
                "calculations instead of doing the arithmetic yourself."
            ),
        ),
        additional_tools=[(PercentageInput, percentage)],
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.from_config(
        settings.agent_key,
        adapter=adapter,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    )

    logger.info("Starting OpenCode agent with a percentage tool")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
