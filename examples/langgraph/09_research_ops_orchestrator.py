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
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
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


ORCHESTRATOR_INSTRUCTIONS = """
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
    """Build a graph_factory compatible with LangGraphAdapter."""
    checkpointer = InMemorySaver()
    compiled_graph: Pregel | None = None
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

    def graph_factory(thenvoi_tools: list[Any]) -> Pregel:
        nonlocal compiled_graph
        if compiled_graph is not None:
            return compiled_graph

        all_tools = thenvoi_tools + [calculator_tool, sql_tool]
        model_with_tools = llm.bind_tools(all_tools)

        async def analyst(state: MessagesState) -> dict[str, list[Any]]:
            messages = [SystemMessage(content=ORCHESTRATOR_INSTRUCTIONS)] + state[
                "messages"
            ]
            response = await model_with_tools.ainvoke(messages)
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
        compiled_graph = builder.compile(checkpointer=checkpointer)
        return compiled_graph

    return graph_factory


async def main() -> None:
    load_dotenv()
    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    agent_id, api_key = load_agent_config("research_ops_agent")
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    logger.info(
        "Creating custom LangGraph operations orchestrator with model %s", model
    )
    adapter = LangGraphAdapter(
        graph_factory=build_orchestrator_factory(ChatOpenAI(model=model)),
        custom_section="Always use the custom operations graph to plan, delegate, and report through Band tools.",
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting custom LangGraph operations orchestrator...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
