# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
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
import sys

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging
from band import Agent
from band.adapters import LangGraphAdapter
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

    features = AdapterFeatures(capabilities={Capability.MEMORY})

    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini")),
        checkpointer=InMemorySaver(),
        custom_section=(
            "Actively look for durable information worth remembering. "
            "When a user states a preference, profile detail, standing instruction, "
            "important project fact, or reusable workflow, call `band_store_memory` "
            "before replying. If you do not have a real subject_id, use "
            'scope="organization" and omit subject_id. Use memory sparingly: do not '
            "store one-off requests, temporary chat context, or sensitive information "
            "unless the user clearly asks you to remember it. After storing a memory, "
            "briefly acknowledge what you saved and continue helping the user."
        ),
        features=features,
    )

    # Create and start agent
    agent = Agent.from_config(
        "memory_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting LangGraph memory tools example agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
