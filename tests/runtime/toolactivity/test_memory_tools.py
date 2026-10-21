from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import ActivityRecorder

MEMORY_ID = "00000000-0000-0000-0000-000000000001"

ToolCall = Callable[[AgentTools], Awaitable[Any]]


def store(tools: AgentTools) -> Awaitable[Any]:
    return tools.store_memory(
        content="remember this",
        system="working",
        type="semantic",
        segment="user",
        thought="useful later",
        scope="organization",
    )


class TestMemoryToolActivity:
    @pytest.mark.parametrize(
        "call",
        [
            store,
            lambda tools: tools.supersede_memory(MEMORY_ID),
            lambda tools: tools.archive_memory(MEMORY_ID),
        ],
        ids=["store", "supersede", "archive"],
    )
    async def test_saving_memory_shows_memory_then_reasoning(
        self, tools: AgentTools, recorder: ActivityRecorder, call: ToolCall
    ) -> None:
        await call(tools)
        assert recorder.events == [{"kind": "memory"}, {"kind": "reasoning"}]

    @pytest.mark.parametrize(
        ("name", "call"),
        [
            (BandTool.GET_MEMORY, lambda tools: tools.get_memory(MEMORY_ID)),
            (BandTool.LIST_MEMORIES, lambda tools: tools.list_memories()),
        ],
        ids=["get", "list"],
    )
    async def test_reading_memory_shows_the_tool_call(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        name: BandTool,
        call: ToolCall,
    ) -> None:
        await call(tools)
        recorder.assert_completed_tool(name)
