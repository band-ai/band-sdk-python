# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
LangGraph agent with memory tools enabled.

This example shows how to configure a LangGraph agent to use Band memory
tools for durable preferences, facts, and reusable instructions.

Try prompts like:
- "Remember that I prefer concise status updates."
- "Remember that this project uses LangGraph for orchestration."
- "What do you remember about my update style?"

Run with (from repo root):
    uv run examples/langgraph/10_memory_tool_usage.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver


from band import Agent, configure_logging
from band.adapters import LangGraphAdapter
from band.core.types import Capability

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini")),
        checkpointer=InMemorySaver(),
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

    logger.info("Starting LangGraph memory tools example agent...")
    async with Agent.from_config(
        "memory_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
