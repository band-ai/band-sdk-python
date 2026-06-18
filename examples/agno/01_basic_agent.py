# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Agno agent example.

Builds a model-agnostic Agno agent and bridges it to the Band platform via
``AgnoAdapter``. The Agno agent owns the model, instructions, and (later) tools;
the adapter converts Band room history into Agno messages and replies with the
agent's text output.

Requires:
    - agent_config.yaml in the working directory with an `agno_agent` entry
      (copy the repo-root agent_config.yaml.example to agent_config.yaml and
      fill in the agno_agent credentials)
    - BAND_WS_URL and BAND_REST_URL environment variables (the platform the
      agent_config.yaml credentials belong to)
    - ANTHROPIC_API_KEY environment variable (for the Claude model)

Run with:
    uv run examples/agno/01_basic_agent.py
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


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

    # Build the Agno agent — you choose the model, instructions, and tools.
    agno_agent = AgnoAgent(
        model=Claude(id="claude-sonnet-4-6"),
        instructions="You are a helpful assistant. Be concise and friendly.",
    )

    # Bridge the Agno agent to Band.
    adapter = AgnoAdapter(agno_agent)

    agent = Agent.from_config(
        "agno_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Agno agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
