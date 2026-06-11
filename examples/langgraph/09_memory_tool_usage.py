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

Run with (from repo root):
    uv run examples/langgraph/09_memory_tool_usage.py
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
from band import Agent, AdapterFeatures, Capability
from band.adapters import LangGraphAdapter

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    features = AdapterFeatures(capabilities={Capability.MEMORY})

    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model="gpt-4o-mini"),
        checkpointer=InMemorySaver(),
        custom_section=(
            "Actively look for durable information worth remembering. "
            "When a user states a preference, profile detail, standing instruction, "
            "important project fact, or reusable workflow, call `band_store_memory` "
            "before replying. Use memory sparingly: do not store one-off requests, "
            "temporary chat context, or sensitive information unless the user clearly "
            "asks you to remember it. After storing a memory, briefly acknowledge what "
            "you saved and continue helping the user."
        ),
        features=features,
    )

    # Create and start agent
    agent = Agent.from_config(
        "simple_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
