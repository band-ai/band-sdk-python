# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]", "anthropic>=0.75.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Agno agent with tool-execution reporting.

Builds an Agno agent that has its own tools. By default the adapter narrates
everything it supports, so whenever the Agno agent calls one of its tools, the
adapter posts tool_call/tool_result events to the room so the tool activity is
visible in Band.

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

from agno.agent import Agent as AgnoAgent
from agno.models.anthropic import Claude
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, LogSettings
from band.adapters import AgnoAdapter


logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    anthropic_api_key: str


def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    # A real tool would call a weather API; this is a stub for the example.
    return f"It is 22°C and sunny in {city}."


async def main() -> None:
    load_dotenv()
    LogSettings().for_application().configure()
    Settings()

    # The Agno agent owns its tools; the adapter reports their executions.
    agno_agent = AgnoAgent(
        model=Claude(id="claude-sonnet-4-6"),
        instructions="You are a helpful assistant. Use tools when relevant.",
        tools=[get_weather],
    )

    # Default emit narrates everything the adapter supports, including
    # tool_call/tool_result events posted to the room.
    adapter = AgnoAdapter(agno_agent)

    logger.info("Starting Agno agent with tool reporting...")
    async with Agent.from_config(
        "agno_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
