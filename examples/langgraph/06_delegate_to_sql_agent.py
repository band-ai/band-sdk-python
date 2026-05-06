# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[langgraph]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Example: Hierarchical agents with graph_as_tool.

This example shows a main agent that can delegate database queries to a SQL subagent.
The SQL subagent has its own LLM and database tools, and all of its internal
execution (tool calls, reasoning, queries) is visible to the user.

Demonstrates:
- Main agent with Thenvoi platform tools
- SQL subagent with its own LLM + database tools
- Full observability of nested execution
- Events bubble up from subagent to main agent
- Real database querying (not mocks)

Run with (from repo root):
    uv run examples/langgraph/06_delegate_to_sql_agent.py

Note: Must be run from repo as it imports standalone_sql_agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from standalone_sql_agent import create_sql_agent, download_chinook_db

from setup_logging import setup_logging
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config
from thenvoi.integrations.langgraph import graph_as_tool

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

    # Load agent configuration from agent_config.yaml
    agent_id, api_key = load_agent_config("sql_agent")

    logger.info("Step 0: Downloading sample database if needed...")
    db_path = download_chinook_db()
    logger.info("Database ready at %s", db_path)

    logger.info(
        "\nStep 1: Creating standalone SQL agent (with its own LLM + database tools)..."
    )
    sql_graph = create_sql_agent(db_path)
    logger.info(
        "SQL agent created - it can list tables, examine schemas, generate queries, and execute them"
    )

    logger.info("\nStep 2: Wrapping SQL agent as a tool...")
    sql_tool = graph_as_tool(
        graph=sql_graph,
        name="database_assistant",
        description="Use this tool to query the database and answer questions about data. It can list tables, examine schemas, and run SQL queries safely.",
        input_schema={
            "messages": "List of messages with the database question. Format: [{'role': 'user', 'content': 'How many employees are there?'}]"
        },
        # Extract the final answer from the SQL agent's messages
        result_formatter=lambda state: (
            state["messages"][-1].content if state.get("messages") else "No result"
        ),
        # Enable memory: subgraph will remember context within the same room
        isolate_thread=False,
    )
    logger.info(
        "SQL agent wrapped as a tool with memory enabled (isolate_thread=False)"
    )

    logger.info("\nStep 3: Creating main Thenvoi agent with database tool...")

    # Create adapter with SQL tool
    adapter = LangGraphAdapter(
        llm=ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o")),
        checkpointer=InMemorySaver(),
        additional_tools=[sql_tool],
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting agent with SQL tool...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
