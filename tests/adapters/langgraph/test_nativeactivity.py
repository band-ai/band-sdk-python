"""Real LangGraph v2 events report native execution with narration disabled."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph

from band.adapters.langgraph import LangGraphAdapter
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool


class TestNativeActivity:
    async def test_native_tool_named_like_band_memory_stays_a_tool_call(
        self, sample_message: PlatformMessage
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def band_store_memory() -> str:
            """Read an external native result."""
            entered.set()
            await release.wait()
            return "native result"

        tool = StructuredTool.from_function(coroutine=band_store_memory)

        async def execute(state: dict[str, Any]) -> dict[str, Any]:
            return {"messages": [await tool.ainvoke({})]}

        graph = StateGraph(dict)
        graph.add_node("execute", execute)
        graph.add_edge(START, "execute")
        graph.add_edge("execute", END)
        compiled = graph.compile()
        adapter = LangGraphAdapter(graph=compiled, emit=())
        tools = FakeAgentTools(room_id=sample_message.room_id)
        recorder = ActivityRecorder()
        recorder.observing(tools)
        running = asyncio.create_task(
            adapter.on_turn(
                [sample_message],
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id=tools.room_id,
            )
        )
        try:
            await entered.wait()
            assert recorder.events == [shown_tool("band_store_memory")]
        finally:
            release.set()
            await running
        assert recorder.events == [shown_tool("band_store_memory"), SHOWN_REASONING]
        assert tool.metadata is None
