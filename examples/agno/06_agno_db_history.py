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
import os

from agno.agent import Agent as AgnoAgent
from agno.db.in_memory import InMemoryDb
from agno.models.anthropic import Claude
from dotenv import load_dotenv

from band import Agent
from band.adapters import AgnoAdapter


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

    db = InMemoryDb()
    session_id = os.environ.get("AGNO_SESSION_ID", "band-agno-db-history")

    agno_agent = AgnoAgent(
        model=Claude(id=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")),
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

    agent = Agent.from_config(
        "agno_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Agno DB-history agent (session_id=%s)...", session_id)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
