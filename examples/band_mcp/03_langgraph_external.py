#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "band-mcp",
#     "langchain>=1.0.0",
#     "langchain-anthropic>=0.3.0",
#     "langchain-mcp-adapters>=0.1.0",
# ]
# ///
"""
LangGraph personal assistant over band-mcp's human scope.

band-mcp serves two distinct tool surfaces (`--scope agent` / `--scope
human`), each with its own credential. The other two examples in this
directory both use `--scope agent` (a bot's own identity, `BAND_AGENT_KEY`).
This one uses `--scope human` with a person's own `BAND_USER_KEY` instead —
a personal-assistant use case with no "agent" in the picture at all, wired
into a framework band-sdk has no adapter relationship with
(`langchain_mcp_adapters.MultiServerMCPClient` spawns `band-mcp` over stdio;
`get_tools()` turns its human-scope tools into ordinary LangChain
`BaseTool`s for a stock `create_agent`).

Prerequisites:
    1. A user-scoped Band API key (BAND_USER_KEY, starts with `band_u_`)
    2. ANTHROPIC_API_KEY

Run with:
    BAND_USER_KEY=band_u_... ANTHROPIC_API_KEY=... \
        uv run examples/band_mcp/03_langgraph_external.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_mcp_adapters.client import MultiServerMCPClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    user_key = os.environ["BAND_USER_KEY"]
    base_url = os.environ.get("BAND_BASE_URL", "https://app.band.ai")

    mcp_client = MultiServerMCPClient(
        {
            "band": {
                "transport": "stdio",
                "command": "band-mcp",
                "args": ["--scope", "human"],
                "env": {"BAND_USER_KEY": user_key, "BAND_BASE_URL": base_url},
            }
        }
    )
    tools = await mcp_client.get_tools()
    logger.info(
        "Loaded %d band-mcp human-scope tools: %s", len(tools), [t.name for t in tools]
    )

    agent = create_agent(ChatAnthropic(model="claude-haiku-4-5"), tools)

    result = await agent.ainvoke(
        {
            "messages": [
                (
                    "user",
                    "List my chat rooms, pick the most recently active one, and read "
                    "its most recent messages. Summarize what's happening there in "
                    "one or two sentences.",
                )
            ]
        }
    )
    logger.info("Personal assistant summary: %s", result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
