# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[google_adk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Google ADK agent with custom tools.

Demonstrates how to add custom tools alongside the platform tools using
the ``additional_tools`` parameter. The adapter bridges them into ADK's
BaseTool system automatically.

Requires Band credentials plus one of:
    - GOOGLE_API_KEY or GOOGLE_GENAI_API_KEY environment variable (Gemini Developer API)
    - gcloud CLI with Application Default Credentials (Vertex AI):
        gcloud auth application-default login
        export GOOGLE_GENAI_USE_VERTEXAI=true
        export GOOGLE_CLOUD_PROJECT=your-project-id

Run with:
    uv run examples/google_adk/03_custom_tools.py
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from dotenv import load_dotenv


from band import Agent, configure_logging
from band.adapters import GoogleADKAdapter
from band.core.types import Emit

configure_logging(logging.INFO)
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


def calculator(input: CalculatorInput) -> str:
    """Execute a calculator operation."""
    ops = {
        "add": lambda a, b: a + b,
        "subtract": lambda a, b: a - b,
        "multiply": lambda a, b: a * b,
        "divide": lambda a, b: "Error: division by zero" if b == 0 else a / b,
    }
    fn = ops.get(input.operation)
    if fn is None:
        return f"Unknown operation '{input.operation}'. Use: add, subtract, multiply, divide"
    result = fn(input.left, input.right)
    return str(result)


class WeatherInput(BaseModel):
    """Get current weather for a city (mock)."""

    city: str = Field(description="Name of the city")


def weather(input: WeatherInput) -> str:
    """Return mock weather data."""
    return f"Weather in {input.city}: Sunny, 22 °C"


async def main() -> None:
    load_dotenv()

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
        emit=Emit.TOOL_CALLS,
    )

    logger.info("Starting Google ADK agent with custom tools...")
    async with Agent.from_config(
        "google_adk_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
