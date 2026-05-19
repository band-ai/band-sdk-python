# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[google_adk]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Google ADK agent with custom tools.

Demonstrates how to add custom tools alongside the platform tools using
the ``additional_tools`` parameter. The adapter bridges them into ADK's
BaseTool system automatically.

Requires GOOGLE_API_KEY (or GOOGLE_GENAI_API_KEY) environment variable for
Gemini authentication, in addition to the Thenvoi credentials.

Run with:
    uv run examples/google_adk/03_custom_tools.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from pydantic import BaseModel, Field

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import GoogleADKAdapter
from thenvoi.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom tool definitions (Pydantic model + handler function)
# ---------------------------------------------------------------------------

class CalculatorInput(BaseModel):
    """Perform a mathematical calculation."""

    operation: str = Field(
        description='The operation: "add", "subtract", "multiply", or "divide"'
    )
    left: float = Field(description="The first number")
    right: float = Field(description="The second number")

def calculator(operation: str, left: float, right: float) -> str:
    """Execute a calculator operation."""
    ops = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: "Error: division by zero" if b == 0 else a / b,
    }
    fn = ops.get(operation)
    if fn is None:
        return f"Unknown operation '{operation}'. Use: add, subtract, multiply, divide"
    result = fn(left, right)
    return str(result)

class WeatherInput(BaseModel):
    """Get current weather for a city (mock)."""

    city: str = Field(description="Name of the city")

def weather(city: str) -> str:
    """Return mock weather data."""
    return f"Weather in {city}: Sunny, 22 °C"

async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")
    # Create adapter with custom tools
    adapter = GoogleADKAdapter(
        model="gemini-2.5-flash",
        additional_tools=[
            (CalculatorInput, calculator),
            (WeatherInput, weather),
        ],
        custom_section=(
            "You are a helpful assistant with access to a calculator and "
            "weather tool in addition to the platform tools."
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.from_config(
        "google_adk_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Google ADK agent with custom tools...")
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
