# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[langgraph]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Custom LangGraph orchestrator with platform tools and subgraph delegation.

This example builds a real LangGraph graph factory instead of using the default
agent graph. The graph can send progress events to Band, delegate math to a
calculator subgraph, delegate database questions to a SQL subgraph, and send the
final answer back through the platform.

Run with (from repo root):
    uv run --extra langgraph python examples/langgraph/09_research_ops_orchestrator.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.pregel import Pregel

from setup_logging import setup_logging
from standalone_calculator import create_calculator_graph
from standalone_sql_agent import create_sql_agent, download_chinook_db
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from thenvoi.config import load_agent_config
from thenvoi.integrations.langgraph import graph_as_tool

setup_logging()
logger = logging.getLogger(__name__)


ORCHESTRATOR_INSTRUCTIONS = """\
You are a Band operations analyst running inside a custom LangGraph graph.

For any substantive request:
1. Use thenvoi_send_event with message_type="thought" to report the plan.
2. Use calculator_math for arithmetic or numeric checks.
3. Use database_assistant for questions about the sample music store database.
4. Use thenvoi_send_message for the final user-visible response.

The platform tools already know the current room from LangGraph config. Do not
ask the user for room IDs.
"""


def build_orchestrator_factory(llm: BaseChatModel) -> Any:
    """Build a graph_factory compatible with LangGraphAdapter.

    The adapter calls ``graph_factory(band_tools)`` on every ``on_message``
    with the tool wrappers bound to the *current room*. We rebuild the graph
    every call so each room gets its own ``ToolNode`` with its own wrappers.
    The compiled graph is cheap to rebuild; the ``InMemorySaver`` checkpointer
    is the one piece we deliberately keep across calls so multi-turn state and
    the bootstrap-once system prompt survive.

    The analyst node reads ``state["messages"]`` directly. The adapter
    prepends the Band-rendered system prompt (which already contains
    :data:`ORCHESTRATOR_INSTRUCTIONS` because it was passed via
    ``custom_section``) on session bootstrap, and the LangGraph
    checkpointer carries it forward across turns.

    Subgraph tools (calculator, SQL) are room-independent and built once.
    """
    checkpointer = InMemorySaver()
    calculator_tool = graph_as_tool(
        graph=create_calculator_graph(),
        name="calculator_math",
        description="Run exact arithmetic in a calculator subgraph.",
        input_schema={
            "operation": "One of add, subtract, multiply, or divide",
            "a": "First number",
            "b": "Second number",
        },
        result_formatter=lambda state: state["result"],
    )

    db_path = download_chinook_db()
    sql_tool = graph_as_tool(
        graph=create_sql_agent(db_path),
        name="database_assistant",
        description="Answer questions about the sample music store database using a SQL LangGraph subagent.",
        input_schema={
            "messages": "List of messages with the database question, for example [{'role': 'user', 'content': 'Which genre has the longest tracks?'}]"
        },
        result_formatter=lambda state: (
            state["messages"][-1].content if state.get("messages") else "No result"
        ),
        isolate_thread=False,
    )

    def graph_factory(band_tools: list[Any]) -> Pregel:
        # Rebuild on every call so each room's tool wrappers (which carry
        # that room's AgentTools) are the ones bound into this graph's
        # ToolNode. Caching here would pin the first room's wrappers and
        # silently route every other room's tool calls back to room A.
        all_tools = band_tools + [calculator_tool, sql_tool]
        model_with_tools = llm.bind_tools(all_tools)

        async def analyst(state: MessagesState) -> dict[str, list[Any]]:
            response = await model_with_tools.ainvoke(state["messages"])
            return {"messages": [response]}

        builder = StateGraph(MessagesState)
        builder.add_node("analyst", analyst)
        builder.add_node("tools", ToolNode(all_tools))
        builder.add_edge(START, "analyst")
        builder.add_conditional_edges(
            "analyst",
            tools_condition,
            {"tools": "tools", END: END},
        )
        builder.add_edge("tools", "analyst")
        return builder.compile(checkpointer=checkpointer)

    return graph_factory


async def main() -> None:
    load_dotenv()
    agent_id, api_key = load_agent_config("research_ops_agent")
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    logger.info(
        "Creating custom LangGraph operations orchestrator with model %s", model
    )
    adapter = LangGraphAdapter(
        graph_factory=build_orchestrator_factory(ChatOpenAI(model=model)),
        custom_section=ORCHESTRATOR_INSTRUCTIONS,
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    )

    logger.info("Starting custom LangGraph operations orchestrator...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
