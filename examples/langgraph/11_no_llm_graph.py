# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[langgraph]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
LangGraph agent with no LLM inside — a deterministic ping/pong graph.

The adapter's "advanced pattern" (``graph_factory=``) accepts any LangGraph
``Pregel`` graph, so a graph never has to call a model. This example replies
"pong" to any message containing "ping" and otherwise echoes the message
content back, sending replies straight through the ``band_send_message``
platform tool from plain Python logic instead of a model-issued tool call.

Run with:
    uv run examples/langgraph/11_no_llm_graph.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.pregel import Pregel

from setup_logging import setup_logging
from band import Agent
from band.adapters import LangGraphAdapter

setup_logging()
logger = logging.getLogger(__name__)

_SENDER_PATTERN = re.compile(r"^\[(?P<sender>[^\]]+)\]:\s*(?P<text>.*)$", re.DOTALL)
_MENTION_TOKEN_PATTERN = re.compile(r"@\[\[[^\]]+\]\]\s*")


def build_no_llm_graph_factory() -> Any:
    """Build a graph_factory whose only node is deterministic Python logic.

    The adapter formats each platform message as ``"[sender]: content"``
    (see ``PlatformMessage.format_for_llm``) before appending it to
    ``state["messages"]``. The node below parses that prefix back out to
    know who to @mention, then calls ``band_send_message`` directly — no
    model, no ``bind_tools``, no ``ToolNode``.
    """
    checkpointer = InMemorySaver()

    def graph_factory(band_tools: list[Any]) -> Pregel:
        send_message = next(t for t in band_tools if t.name == "band_send_message")

        async def reply(state: MessagesState) -> dict[str, list[Any]]:
            last = state["messages"][-1]
            match = _SENDER_PATTERN.match(str(last.content))
            sender = match.group("sender") if match else "there"
            text = match.group("text") if match else str(last.content)
            # Strip raw "@[[uuid]]" mention tokens before echoing: the echoed
            # text isn't registered in this reply's `mentions` list, so an
            # unresolved token would render as "@Unknown" in the chat UI.
            text = _MENTION_TOKEN_PATTERN.sub("", text).strip()

            reply_text = "pong" if "ping" in text.lower() else f"You said: {text}"
            await send_message.ainvoke(
                {"content": f"{reply_text}", "mentions": [sender]}
            )
            return {"messages": []}

        builder = StateGraph(MessagesState)
        builder.add_node("reply", reply)
        builder.add_edge(START, "reply")
        builder.add_edge("reply", END)
        return builder.compile(checkpointer=checkpointer)

    return graph_factory


async def main() -> None:
    load_dotenv()
    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    adapter = LangGraphAdapter(
        graph_factory=build_no_llm_graph_factory(), enable_execution_reporting=True
    )

    agent = Agent.from_config(
        "no_llm_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting no-LLM LangGraph agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
