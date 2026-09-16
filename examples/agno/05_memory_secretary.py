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

from agno.agent import Agent as AgnoAgent
from agno.models.anthropic import Claude
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, LogSettings
from band.adapters import AgnoAdapter
from band.core.types import Capability


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-6"


async def main() -> None:
    load_dotenv()
    LogSettings().for_application().configure()
    settings = Settings()

    agno_agent = AgnoAgent(
        model=Claude(id=settings.anthropic_model),
        instructions=SECRETARY_INSTRUCTIONS,
    )

    # capabilities=Capability.MEMORY exposes Band memory tools. Emit defaults
    # to everything the adapter supports, so memory tool calls are visible as
    # room events without an explicit emit=.
    adapter = AgnoAdapter(agno_agent, capabilities=Capability.MEMORY)

    logger.info("Starting Agno memory secretary...")
    async with Agent.from_config(
        "agno_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
