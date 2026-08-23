# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
CrewAI agent with memory tools enabled.

This example shows how to configure a CrewAI agent to use Band memory
tools for durable preferences, facts, and reusable instructions.

Configure the model and provider-specific settings with environment variables.

Try prompts like:
- "Remember that I prefer concise status updates."
- "Remember that this project uses CrewAI for orchestration."
- "What do you remember about my update style?"

Run with (from repo root):
    uv run examples/crewai/10_memory_tool_usage.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import CrewAIAdapter
from band.core.types import Capability

configure_logging(logging.INFO, extra_loggers={"band_crewai_agent": logging.INFO})
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    crewai_model: str


async def main() -> None:
    load_dotenv()
    settings = Settings()
    model = settings.crewai_model

    adapter = CrewAIAdapter(
        model=model,
        role="Memory-aware assistant",
        goal=(
            "Help users while remembering durable preferences, facts, and "
            "reusable instructions that survive beyond the current conversation."
        ),
        backstory=(
            "You support ongoing collaboration inside Band rooms and know how to "
            "use memory tools sparingly for durable context."
        ),
        custom_section=(
            "Actively look for durable information worth remembering. "
            "When a user states a preference, profile detail, standing instruction, "
            "important project fact, or reusable workflow, call `band_store_memory` "
            "before replying. Use memory sparingly: do not store one-off requests, "
            "temporary chat context, or sensitive information unless the user clearly "
            "asks you to remember it. After storing a memory, briefly acknowledge "
            "what you saved and continue helping the user."
        ),
        capabilities=Capability.MEMORY,
    )

    logger.info("Starting CrewAI memory tools example agent (model=%s)...", model)
    async with Agent.from_config(
        "memory_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
