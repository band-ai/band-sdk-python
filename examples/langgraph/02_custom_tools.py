# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Example showing how to add custom tools to a Band agent.

The composition architecture makes it trivial to add your own tools alongside
the platform tools.

Run with:
    uv run examples/langgraph/02_custom_tools.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from setup_logging import setup_logging
from band import Agent
from band.adapters import LangGraphAdapter

setup_logging()
logger = logging.getLogger(__name__)


# Define custom tools
@tool
def calculate(operation: str, left: float, right: float) -> str:
    """Perform a mathematical calculation safely.

    Args:
        operation: The operation to perform: "add", "subtract", "multiply", "divide", or "power"
        left: The first number
        right: The second number
    """
    try:
        if operation == "add":
            result = left + right
        elif operation == "subtract":
            result = left - right
        elif operation == "multiply":
            result = left * right
        elif operation == "divide":
            if right == 0:
                return "Error: Cannot divide by zero"
            result = left / right
        elif operation == "power":
            result = left**right
        else:
            return f"Error: Unknown operation '{operation}'. Use: add, subtract, multiply, divide, or power"

        return f"Result: {result}"
    except Exception as e:
        return f"Error: {e}"


@tool
def get_weather(city: str) -> str:
    """Get weather for a city (mock implementation).

    Args:
        city: Name of the city
    """
    # In real implementation, call weather API
    return f"Weather in {city}: Sunny, 72°F"


async def main() -> None:
    load_dotenv()
    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
    # Create adapter with custom tools
    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini")),
        checkpointer=InMemorySaver(),
        additional_tools=[calculate, get_weather],  # Add your tools here
        custom_section="""You are a helpful assistant with access to:
        - Platform tools (band_send_message, band_add_participant, etc.)
        - Calculator tool for math
        - Weather tool for weather info

        When users ask math questions, use the calculator.
        When users ask about weather, use get_weather.
        Always send your response using band_send_message.""",
    )

    # Create and start agent
    agent = Agent.from_config(
        "custom_tools_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting agent with custom tools...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
