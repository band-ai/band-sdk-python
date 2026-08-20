# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[agno]", "anthropic>=0.75.0"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Agno-owned conversation history with a database.

This example configures Agno to persist and replay prior turns itself by using
``db=...``, ``session_id=...``, and ``add_history_to_context=True``. When
``AgnoAdapter`` detects this mode, it disables Band history rehydration for the
model input so the same prior turns are not injected twice.

The example uses Agno's in-memory database so it is easy to run. It preserves
history only while this process is alive. For production, replace ``InMemoryDb``
with a persistent Agno database and keep the same session-id strategy.

Try prompts like:
- "Remember that the release checklist lives in Notion page R-42."
- "What checklist page did I mention?"

Requires:
    - agent_config.yaml in the working directory with an `agno_agent` entry
      (copy the repo-root agent_config.yaml.example to agent_config.yaml and
      fill in the agno_agent credentials)
    - BAND_WS_URL and BAND_REST_URL environment variables (the platform the
      agent_config.yaml credentials belong to)
    - ANTHROPIC_API_KEY environment variable (for the Claude model)

Run with:
    uv run examples/agno/06_agno_db_history.py
"""

from __future__ import annotations

import asyncio
import logging

from agno.agent import Agent as AgnoAgent
from agno.db.in_memory import InMemoryDb
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
    anthropic_model: str = "claude-sonnet-4-6"
    agno_session_id: str = "band-agno-db-history"


async def main() -> None:
    load_dotenv()
    LogSettings().for_application().configure()
    settings = Settings()

    db = InMemoryDb()
    session_id = settings.agno_session_id

    agno_agent = AgnoAgent(
        model=Claude(id=settings.anthropic_model),
        db=db,
        session_id=session_id,
        add_history_to_context=True,
        instructions=(
            "You are a helpful assistant with Agno-managed conversation history. "
            "When acknowledging or recalling a value the user asked you to "
            "remember, include the exact value in your reply. Keep responses "
            "short."
        ),
    )

    adapter = AgnoAdapter(
        agno_agent,
        # AgnoAdapter passes session_id on each run. This keeps the example tied
        # to the Agno session configured above instead of defaulting to room_id.
        session_id_factory=lambda _room_id: session_id,
    )

    logger.info("Starting Agno DB-history agent (session_id=%s)...", session_id)
    async with Agent.from_config(
        "agno_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
