# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Example: Using graph_as_tool to wrap a standalone graph as a tool.

This example demonstrates:
1. Importing a standalone, compiled graph (calculator)
2. Wrapping it as a tool using graph_as_tool
3. Adding it to a Band agent alongside platform tools
4. The agent intelligently decides when to use the calculator

The calculator graph knows nothing about Band - it's completely independent.

Run with (from repo root):
    uv run examples/langgraph/04_calculator_as_tool.py

Note: Must be run from repo as it imports standalone_calculator.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from standalone_calculator import create_calculator_graph

from setup_logging import setup_logging
from band import Agent
from band.adapters import LangGraphAdapter
from band.config import load_agent_config
from band.integrations.langgraph import graph_as_tool

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
    agent_id, api_key = load_agent_config("calculator_agent")

    logger.info(
        "Step 1: Creating standalone calculator graph (no Band dependencies)..."
    )
    calculator_graph = create_calculator_graph()
    logger.info("Calculator graph created and compiled")

    logger.info("Step 2: Wrapping calculator graph as a tool...")
    calculator_tool = graph_as_tool(
        graph=calculator_graph,
        name="calculator",
        description="Use this tool to perform mathematical calculations. It can add, subtract, multiply, and divide numbers.",
        input_schema={
            "operation": "The math operation to perform: 'add', 'subtract', 'multiply', or 'divide'",
            "a": "The first number",
            "b": "The second number",
        },
        # Format the result nicely for the agent
        result_formatter=lambda state: f"Calculation result: {state['result']}",
    )
    logger.info("Calculator wrapped as a tool")

    logger.info("Step 3: Creating Band agent with calculator tool...")

    # Create adapter with calculator tool
    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model="gpt-4o"),
        checkpointer=InMemorySaver(),
        additional_tools=[calculator_tool],
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting agent with calculator tool...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
