# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[strands]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Strands agent with custom tools and capabilities.

Shows the portable ``CustomToolDef`` (InputModel, handler) form shared across
adapters, plus enabling memory/contact tools and execution/usage event
emission via AdapterFeatures.

Run with:
    uv run examples/strands/02_custom_tools.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from pydantic import BaseModel
from strands.models.openai import OpenAIModel

from setup_logging import setup_logging
from band import Agent
from band.adapters import StrandsAdapter
from band.core.types import AdapterFeatures, Capability, Emit

setup_logging()
logger = logging.getLogger(__name__)


class WeatherInput(BaseModel):
    """Get the weather for a city."""

    city: str


async def get_weather(args: WeatherInput) -> str:
    return f"{args.city}: sunny, 22°C"


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    adapter = StrandsAdapter(
        model=OpenAIModel(model_id="gpt-5.4-mini"),
        custom_section="You can check the weather with the weather tool.",
        additional_tools=[(WeatherInput, get_weather)],  # CustomToolDef tuple
        features=AdapterFeatures(
            emit=frozenset({Emit.EXECUTION, Emit.USAGE}),
            capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS}),
        ),
    )

    agent = Agent.from_config(
        "strands_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Strands agent with custom tools...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
