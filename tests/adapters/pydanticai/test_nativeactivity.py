"""Native Pydantic tools report actual execution independently of narration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel

from band.adapters.pydantic_ai import PydanticAIAdapter
from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from tests.adapters.pydanticai.kit import _run_on_message
from tests.adapters.pydanticai.nativeactivity import NATIVE_FAILURE, fail, succeed


class TestNativeActivity:
    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            (succeed, nullcontext()),
            (fail, pytest.raises(ValueError, match=NATIVE_FAILURE)),
        ],
        ids=["success", "error"],
    )
    async def test_native_context_tool_is_shown_during_its_actual_body(
        self,
        sample_message: PlatformMessage,
        outcome: Callable[[], str],
        expected: AbstractContextManager[object],
    ) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        tools = FakeAgentTools(room_id=sample_message.room_id)
        recorder = ActivityRecorder()
        recorder.observing(tools)

        async def native_lookup(ctx: RunContext[AgentToolsProtocol]) -> str:
            assert ctx.deps is tools
            entered.set()
            await release.wait()
            return outcome()

        adapter = PydanticAIAdapter(
            llm=TestModel(call_tools=["native_lookup"]),
            additional_tools=[native_lookup],
            emit=(),
        )
        running = asyncio.create_task(_run_on_message(adapter, sample_message, tools))
        try:
            await entered.wait()
            assert recorder.events == [shown_tool("native_lookup")]
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
        with expected:
            await running
        assert recorder.events == [shown_tool("native_lookup"), SHOWN_REASONING]
