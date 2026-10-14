"""Batch-input probes for the framework agent-loop adapters; see ``batchprobes``.

Each probe runs the framework's own loop (a compiled LangGraph graph, a real
Agno agent, a Strands agent, a pydantic-ai agent) against a scripted model and
reads back the messages the model was asked to respond to, system prompt
excluded.
"""

from __future__ import annotations

from typing import Any

import pytest

from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver


async def run_langgraph(inp: AgentInput, final_text: str) -> str:
    pytest.importorskip("langgraph")
    from langchain_core.messages import (  # noqa: PLC0415 -- langgraph extra, absent from some lane venvs
        AIMessage,
    )
    from langgraph.graph import (  # noqa: PLC0415 -- langgraph extra, absent from some lane venvs
        END,
        START,
        MessagesState,
        StateGraph,
    )

    from band.adapters.langgraph import (  # noqa: PLC0415 -- langgraph extra, absent from some lane venvs
        LangGraphAdapter,
    )

    received: list[Any] = []

    async def model(state: MessagesState) -> dict[str, Any]:
        received[:] = state["messages"]
        return {"messages": [AIMessage(content=final_text)]}

    graph = (
        StateGraph(MessagesState)
        .add_node("model", model)
        .add_edge(START, "model")
        .add_edge("model", END)
        .compile()
    )
    adapter = LangGraphAdapter(graph_factory=lambda _turn_tools: graph)
    await adapter.on_started("Agent", "A test agent")
    await deliver(adapter, inp)
    return "\n".join(message.content for message in received)


PROBES: dict[str, BatchProbe] = {
    "langgraph": BatchProbe(run=run_langgraph),
}
