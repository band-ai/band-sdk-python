# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Tom and Jerry — two Agno character agents in one process.

Spins up both Tom (the cat) and Jerry (the mouse) as separate Band agents,
each backed by its own Agno agent with a distinct personality, and runs them
concurrently with asyncio.gather.

Add both agents to the same Band room and mention them: they reply in character
and bicker back and forth. Each agent has the Band toolset, so they can also
look up and invite each other, then keep the chase going.

Requires:
    - agent_config.yaml with `tom` and `jery` entries (agent_id + api_key)
    - BAND_WS_URL and BAND_REST_URL environment variables
    - ANTHROPIC_API_KEY environment variable (for the Claude model)

Run with (from repo root):
    uv run examples/agno/03_tom_and_jerry.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from agno.agent import Agent as AgnoAgent
from agno.models.anthropic import Claude
from dotenv import load_dotenv

from band import Agent
from band.adapters import AgnoAdapter
from band.core.types import AdapterFeatures, Emit


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prompts.characters import generate_jerry_prompt, generate_tom_prompt


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


def build_agent(
    config_key: str, instructions: str, ws_url: str, rest_url: str
) -> Agent:
    """Build a Band agent backed by an in-character Agno agent."""
    agno_agent = AgnoAgent(
        model=Claude(id="claude-sonnet-4-6"),
        instructions=instructions,
    )
    return Agent.from_config(
        config_key,
        # emit=EXECUTION posts tool_call/tool_result events so the agents'
        # platform actions (lookup, invite, send) are visible in the room.
        adapter=AgnoAdapter(
            agno_agent, features=AdapterFeatures(emit={Emit.EXECUTION})
        ),
        ws_url=ws_url,
        rest_url=rest_url,
    )


async def main() -> None:
    ws_url, rest_url = load_environment()

    tom = build_agent("tom", generate_tom_prompt("Tom", "Jerry"), ws_url, rest_url)
    jerry = build_agent("jery", generate_jerry_prompt("Jerry", "Tom"), ws_url, rest_url)

    logger.info("Starting Tom and Jerry...")
    await asyncio.gather(tom.run(), jerry.run())


if __name__ == "__main__":
    asyncio.run(main())
