# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[langgraph]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Simple LangGraph agent example using the composition API.

This is the simplest way to create a Thenvoi agent - just provide
the LLM and checkpointer, and the adapter handles everything.

Run with:
    uv run examples/langgraph/01_simple_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config

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

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("simple_agent")

    # Create adapter with LLM and checkpointer
    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o")),
        checkpointer=InMemorySaver(),
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting LangGraph agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
