from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from band.runtime.tools import AgentTools
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_MEMORY, SHOWN_REASONING, ActivityRecorder

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


SAVES = [
    (store, "create_agent_memory"),
    (lambda tools: tools.supersede_memory(MEMORY_ID), "supersede_agent_memory"),
    (lambda tools: tools.archive_memory(MEMORY_ID), "archive_agent_memory"),
]
SAVE_IDS = ["store", "supersede", "archive"]


class TestMemoryToolActivity:
    @pytest.mark.parametrize(("call", "rest_method"), SAVES, ids=SAVE_IDS)
    async def test_saving_memory_shows_memory_then_reasoning(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
        call: ToolCall,
        rest_method: str,
    ) -> None:
        await call(tools)
        getattr(mock_rest_client.agent_api_memories, rest_method).assert_awaited_once()
        assert recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]

    @pytest.mark.parametrize(("call", "rest_method"), SAVES, ids=SAVE_IDS)
    async def test_a_failed_save_still_returns_to_reasoning(
        self,
        tools: AgentTools,
        recorder: ActivityRecorder,
        mock_rest_client: MagicMock,
        call: ToolCall,
        rest_method: str,
    ) -> None:
        save = getattr(mock_rest_client.agent_api_memories, rest_method)
        save.side_effect = RuntimeError("memory store down")
        with pytest.raises(RuntimeError, match="memory store down"):
            await call(tools)
        assert recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]

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
