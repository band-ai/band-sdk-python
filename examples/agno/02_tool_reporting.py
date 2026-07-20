# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]", "anthropic>=0.75.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Agno agent with tool-execution reporting.

Builds an Agno agent that has its own tools, and enables Band execution
reporting via ``AdapterFeatures(emit={Emit.EXECUTION})``. Whenever the Agno
agent calls one of its tools, the adapter posts tool_call/tool_result events to
the room so the tool activity is visible in Band.

Requires:
    - agent_config.yaml in the working directory with an `agno_agent` entry
      (copy the repo-root agent_config.yaml.example to agent_config.yaml and
      fill in the agno_agent credentials)
    - BAND_WS_URL and BAND_REST_URL environment variables (the platform the
      agent_config.yaml credentials belong to)
    - ANTHROPIC_API_KEY environment variable (for the Claude model)

Run with:
    uv run examples/agno/02_tool_reporting.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from agno.agent import Agent as AgnoAgent
from agno.models.anthropic import Claude
from dotenv import load_dotenv

from band import Agent
from band.adapters import AgnoAdapter
from band.core.types import AdapterFeatures, Emit


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    # A real tool would call a weather API; this is a stub for the example.
    return f"It is 22°C and sunny in {city}."


def load_environment() -> tuple[str, str]:
    """Load env vars, validate credentials, and return (ws_url, rest_url)."""
    load_dotenv()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError("ANTHROPIC_API_KEY environment variable is required")

    ws_url = os.environ.get("BAND_WS_URL")
    rest_url = os.environ.get("BAND_REST_URL")
    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
    return ws_url, rest_url


async def main() -> None:
    ws_url, rest_url = load_environment()

    # The Agno agent owns its tools; the adapter reports their executions.
    agno_agent = AgnoAgent(
        model=Claude(id="claude-sonnet-4-6"),
        instructions="You are a helpful assistant. Use tools when relevant.",
        tools=[get_weather],
    )

    # emit={Emit.EXECUTION} posts tool_call/tool_result events to the room.
    adapter = AgnoAdapter(
        agno_agent,
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    agent = Agent.from_config(
        "agno_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Agno agent with tool reporting...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
