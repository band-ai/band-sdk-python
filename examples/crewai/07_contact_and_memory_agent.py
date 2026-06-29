# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[crewai]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
CrewAI agent with contact and memory tools enabled.

This example shows a CrewAI adapter configured to:
- use contact tools through normal LLM tool calling
- enable memory tools for durable preferences and notes
- broadcast contact changes back into active rooms

Try prompts like:
- "List my contacts and check whether @alice is already connected."
- "Send a contact request to @alice with a short intro."
- "Remember that I want concise status updates."
- "What do you remember about my preferred update style?"

Run with:
    uv run examples/crewai/07_contact_and_memory_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import CrewAIAdapter
from band.runtime.types import ContactEventConfig, ContactEventStrategy
from band.core.types import AdapterFeatures, Capability

setup_logging()
logger = logging.getLogger(__name__)


def get_required_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


async def main() -> None:
    load_dotenv()

    ws_url = get_required_env("BAND_WS_URL")
    rest_url = get_required_env("BAND_REST_URL")
    adapter = CrewAIAdapter(
        model="gpt-5.4-mini",
        role="Contact-aware relationship manager",
        goal=(
            "Help users manage contacts, keep track of relationship context, "
            "and remember durable preferences when that is useful."
        ),
        backstory=(
            "You support ongoing collaboration inside Band rooms. "
            "You know how to inspect contacts, manage contact requests, "
            "and use memory tools sparingly for durable context."
        ),
        custom_section=(
            "Use contact tools when the user asks about who they know, who to add, "
            "or the state of a contact request. "
            "Use memory tools for durable user preferences, follow-up notes, or "
            "important facts that should survive beyond the current turn. "
            "When a system message reports that a contact was added or removed, "
            "treat it as fresh room context."
        ),
        features=AdapterFeatures(capabilities={Capability.MEMORY}),
    )

    contact_config = ContactEventConfig(
        strategy=ContactEventStrategy.DISABLED,
        broadcast_changes=True,
    )

    agent = Agent.from_config(
        "crewai_contact_memory_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
        contact_config=contact_config,
    )

    logger.info("Starting CrewAI contact-and-memory example agent")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
