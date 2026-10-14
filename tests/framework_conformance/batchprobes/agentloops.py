"""Batch-input probes for the framework agent-loop adapters; see ``batchprobes``.

Each probe runs the framework's own loop (a compiled LangGraph graph, a real
Agno agent, a Strands agent, a pydantic-ai agent) against a scripted model and
reads back the messages the model was asked to respond to, system prompt
excluded.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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


async def run_pydantic_ai(inp: AgentInput, final_text: str) -> str:
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import (  # noqa: PLC0415 -- pydantic_ai extra, absent from some lane venvs
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )
    from pydantic_ai.models.function import (  # noqa: PLC0415 -- pydantic_ai extra, absent from some lane venvs
        AgentInfo,
        FunctionModel,
    )

    from band.adapters.pydantic_ai import (  # noqa: PLC0415 -- pydantic_ai extra, absent from some lane venvs
        PydanticAIAdapter,
    )

    received: list[str] = []

    async def stream(
        messages: list[ModelRequest | ModelResponse], info: AgentInfo
    ) -> AsyncIterator[str]:
        received[:] = [
            str(part.content)
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart | TextPart)
        ]
        yield final_text

    adapter = PydanticAIAdapter(llm=FunctionModel(stream_function=stream))
    await adapter.on_started("Agent", "A test agent")
    await deliver(adapter, inp)
    return "\n".join(received)


PROBES: dict[str, BatchProbe] = {
    "langgraph": BatchProbe(run=run_langgraph),
    "pydantic_ai": BatchProbe(run=run_pydantic_ai),
}
