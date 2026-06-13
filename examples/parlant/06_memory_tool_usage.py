# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[parlant]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Parlant agent with memory tools enabled.

This example shows how to configure a Parlant agent to use Band memory tools
for durable preferences, facts, and reusable instructions.

Try prompts like:
- "Remember that I prefer concise status updates."
- "Remember that this project uses Parlant for guideline-based behavior."
- "What do you remember about my update style?"

Run with (from repo root):
    uv run examples/parlant/06_memory_tool_usage.py
"""

from __future__ import annotations

import asyncio
import logging
import os

import parlant.sdk as p
from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import ParlantAdapter
from band.core.types import AdapterFeatures, Capability
from band.integrations.parlant.tools import create_parlant_tools

setup_logging()
logger = logging.getLogger(__name__)


MEMORY_DESCRIPTION = """
You are a helpful assistant in the Band multi-agent platform.

Use Band memory tools for durable information that should survive beyond the
current conversation, including stable user preferences, reusable workflow
instructions, and important project facts.

Use memory sparingly. Do not store one-off requests, temporary chat context, or
sensitive information unless the user clearly asks you to remember it.
"""


def get_required_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable is required")
    return value


async def setup_memory_agent(server: p.Server, tools: list) -> p.Agent:
    """Create and configure a Parlant agent with memory-focused guidelines."""
    agent = await server.create_agent(
        name="Parlant Memory",
        description=MEMORY_DESCRIPTION,
    )

    await agent.create_guideline(
        condition="User asks a question or sends a message",
        action=(
            "Respond using band_send_message with the user's name in mentions. "
            "If stored memories may be relevant, first use band_list_memories to "
            "look them up."
        ),
        tools=tools,
    )

    await agent.create_guideline(
        condition=(
            "User states a durable preference, profile detail, standing "
            "instruction, important project fact, or reusable workflow"
        ),
        action=(
            "Call band_store_memory before replying. Use system='long_term', "
            "memory_type='semantic' for facts and preferences or "
            "memory_type='procedural' for reusable workflows, segment='user', "
            "and scope='organization'. Briefly acknowledge what you saved."
        ),
        tools=tools,
    )

    await agent.create_guideline(
        condition="User asks what you remember or asks about stored preferences",
        action=(
            "Use band_list_memories with a focused content_query, then summarize "
            "the relevant stored memories. If nothing relevant is found, say so."
        ),
        tools=tools,
    )

    await agent.create_guideline(
        condition="User says a remembered fact or preference is outdated",
        action=(
            "If you can identify the stale memory, use band_supersede_memory or "
            "band_archive_memory as appropriate, then store the updated memory "
            "when the user provides replacement durable information."
        ),
        tools=tools,
    )

    return agent


async def main() -> None:
    load_dotenv()

    ws_url = get_required_env("BAND_WS_URL")
    rest_url = get_required_env("BAND_REST_URL")
    features = AdapterFeatures(capabilities={Capability.MEMORY})

    async with p.Server(nlp_service=p.NLPServices.openai) as server:
        parlant_tools = create_parlant_tools(features=features)
        logger.info(
            "Created %s Parlant memory tools: %s",
            len(parlant_tools),
            [t.tool.name for t in parlant_tools],
        )

        parlant_agent = await setup_memory_agent(server, parlant_tools)
        logger.info("Parlant memory agent created: %s", parlant_agent.id)

        adapter = ParlantAdapter(
            server=server,
            parlant_agent=parlant_agent,
            custom_section=(
                "Actively look for durable information worth remembering. "
                "Use the memory tools only for stable preferences, reusable "
                "instructions, and important facts."
            ),
            features=features,
        )

        agent = Agent.from_config(
            "parlant_agent",
            adapter=adapter,
            ws_url=ws_url,
            rest_url=rest_url,
        )

        logger.info("Starting Parlant memory tools example agent...")
        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
