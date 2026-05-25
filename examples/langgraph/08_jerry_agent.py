# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[langgraph]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Jerry the mouse agent using LangGraph.

This example shows how to create a character agent with a custom personality
using LangGraph. Jerry is a clever mouse who lives in his hole
and teases Tom the cat while staying safe from being caught.

Run with (from repo root):
    uv run examples/langgraph/08_jerry_agent.py

Note: Must be run from repo as it imports prompts/characters.py
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

from prompts.characters import generate_jerry_prompt

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    # Load Jerry's credentials from agent_config.yaml
    # Create adapter with Jerry's character prompt
    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model="gpt-5.4-mini"),
        checkpointer=InMemorySaver(),
        custom_section=generate_jerry_prompt("Jerry"),
    )

    # Create and start agent
    agent = Agent.from_config(
        "jerry_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Jerry is cozy in his hole, watching for Tom...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
