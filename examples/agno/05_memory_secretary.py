# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]", "anthropic>=0.75.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Agno agent with Band memory tools enabled.

This example gives an Agno "secretary" agent access to Band memory tools via
``Capability.MEMORY``. The agent can store durable preferences, profile facts,
standing instructions, and reusable project context, then recall them in later
conversations.

Try prompts like:
- "Remember that I prefer concise status updates."
- "Remember this for the whole organization: our Q3 launch codename is Cedar."
- "What do you remember about my update style?"

Requires:
    - agent_config.yaml in the working directory with an `agno_agent` entry
      (copy the repo-root agent_config.yaml.example to agent_config.yaml and
      fill in the agno_agent credentials)
    - BAND_WS_URL and BAND_REST_URL environment variables (the platform the
      agent_config.yaml credentials belong to)
    - ANTHROPIC_API_KEY environment variable (for the Claude model)

Run with:
    uv run examples/agno/05_memory_secretary.py
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
from band.core.types import AdapterFeatures, Capability, Emit


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SECRETARY_INSTRUCTIONS = (
    "You are a personal secretary who helps the user preserve useful long-term "
    "context. Actively look for durable information worth remembering: user "
    "preferences, profile details, standing instructions, important project "
    "facts, and reusable workflows. When the user shares something durable, use "
    "Band memory tools to store it before replying. Use memory sparingly: do not "
    "store one-off requests, temporary chat context, or sensitive information "
    "unless the user clearly asks you to remember it. When asked what you "
    "remember, use Band memory tools to search before answering. Keep responses "
    "short."
)


def get_required_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


def load_environment() -> tuple[str, str]:
    """Load env vars, validate credentials, and return (ws_url, rest_url)."""
    load_dotenv()

    get_required_env("ANTHROPIC_API_KEY")
    ws_url = get_required_env("BAND_WS_URL")
    rest_url = get_required_env("BAND_REST_URL")
    return ws_url, rest_url


async def main() -> None:
    ws_url, rest_url = load_environment()

    agno_agent = AgnoAgent(
        model=Claude(id=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")),
        instructions=SECRETARY_INSTRUCTIONS,
    )

    adapter = AgnoAdapter(
        agno_agent,
        features=AdapterFeatures(
            capabilities={Capability.MEMORY},
            # Useful while learning: memory tool calls are visible as room events.
            emit={Emit.EXECUTION},
        ),
    )

    agent = Agent.from_config(
        "agno_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Agno memory secretary...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
