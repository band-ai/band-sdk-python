"""Batch-input probes for the framework agent-loop adapters; see ``batchprobes``.

Each probe runs the framework's own loop (a compiled LangGraph graph, a real
Agno agent, a Strands agent, a pydantic-ai agent) against a scripted model and
reads back the messages the model was asked to respond to, system prompt
excluded.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
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


async def run_agno(inp: AgentInput, final_text: str) -> str:
    pytest.importorskip("agno")
    from agno.agent import (  # noqa: PLC0415 -- agno extra, absent from some lane venvs
        Agent,
    )

    from band.adapters.agno import (  # noqa: PLC0415 -- agno extra, absent from some lane venvs
        AgnoAdapter,
    )
    from tests.adapters.agno.helpers import (  # noqa: PLC0415 -- agno extra, absent from some lane venvs
        CapturingModel,
    )

    model = CapturingModel(final_text)
    # emit=() keeps the run non-streaming, the only path CapturingModel serves.
    adapter = AgnoAdapter(agent=Agent(model=model, telemetry=False), emit=())
    await adapter.on_started("Agent", "A test agent")
    await deliver(adapter, inp)
    return "\n".join(
        str(message.content)
        for message in model.captured_messages or []
        if message.role != "system"
    )


async def run_strands(inp: AgentInput, final_text: str) -> str:
    pytest.importorskip("strands")
    from strands.types.content import (  # noqa: PLC0415 -- strands extra, absent from some lane venvs
        Messages,
    )
    from strands.types.streaming import (  # noqa: PLC0415 -- strands extra, absent from some lane venvs
        StreamEvent,
    )
    from strands.types.tools import (  # noqa: PLC0415 -- strands extra, absent from some lane venvs
        ToolSpec,
    )

    from band.adapters.strands import (  # noqa: PLC0415 -- strands extra, absent from some lane venvs
        StrandsAdapter,
    )
    from band.testing import (  # noqa: PLC0415 -- strands extra, absent from some lane venvs
        ScriptedStrandsModel,
        TextTurn,
    )

    class RecordingModel(ScriptedStrandsModel):
        received: Messages

        async def stream(
            self,
            messages: Messages,
            tool_specs: list[ToolSpec] | None = None,
            system_prompt: str | None = None,
            **kwargs: Any,
        ) -> AsyncGenerator[StreamEvent, None]:
            self.received = list(messages)
            async for event in super().stream(
                messages, tool_specs, system_prompt, **kwargs
            ):
                yield event

    model = RecordingModel([TextTurn(final_text)])
    adapter = StrandsAdapter(llm=model)
    await adapter.on_started("Agent", "A test agent")
    await deliver(adapter, inp)
    return "\n".join(
        block["text"]
        for message in model.received
        for block in message["content"]
        if "text" in block
    )


PROBES: dict[str, BatchProbe] = {
    "langgraph": BatchProbe(run=run_langgraph),
    "pydantic_ai": BatchProbe(run=run_pydantic_ai),
    "agno": BatchProbe(run=run_agno),
    "strands": BatchProbe(run=run_strands),
}
