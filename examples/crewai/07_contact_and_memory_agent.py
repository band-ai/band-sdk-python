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

from dotenv import load_dotenv

from band import Agent, configure_logging
from band.adapters import CrewAIAdapter
from band.runtime.types import ContactEventConfig, ContactEventStrategy
from band.core.types import Capability

configure_logging(logging.INFO, extra_loggers={"band_crewai_agent": logging.INFO})
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

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
        capabilities=Capability.MEMORY,
    )

    contact_config = ContactEventConfig(
        strategy=ContactEventStrategy.DISABLED,
        broadcast_changes=True,
    )

    logger.info("Starting CrewAI contact-and-memory example agent")
    async with Agent.from_config(
        "crewai_contact_memory_agent",
        adapter=adapter,
        contact_config=contact_config,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
