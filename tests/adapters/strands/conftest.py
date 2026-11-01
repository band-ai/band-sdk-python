"""Strands fixtures load their optional framework only when requested."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools

if TYPE_CHECKING or find_spec("strands") is not None:
    from strands import tool as strands_tool

    from band.adapters.strands import StrandsAdapter
    from band.testing.strands import ScriptedStrandsModel, ScriptedTurn
    from tests.adapters.strands.activitypeer import (
        HeldFollowupModel,
        NativeActivityTurn,
    )
    from tests.adapters.strands.kit import ROOM, _run_message


@pytest.fixture
def tools() -> FakeAgentTools:
    return FakeAgentTools(room_id=ROOM)


@pytest.fixture
def scripted() -> Callable[..., Awaitable[StrandsAdapter]]:
    async def build(
        *turns: ScriptedTurn,
        input_tokens: int = 0,
        output_tokens: int = 0,
        **adapter_args: Any,
    ) -> StrandsAdapter:
        adapter = StrandsAdapter(
            llm=ScriptedStrandsModel(
                turns, input_tokens=input_tokens, output_tokens=output_tokens
            ),
            **adapter_args,
        )
        await adapter.on_started("Bot", "A bot")
        return adapter

    return build


@pytest_asyncio.fixture(loop_scope="function")
async def native_activity_turn(
    tools: FakeAgentTools,
) -> AsyncIterator[NativeActivityTurn]:
    entered = asyncio.Event()
    exited = asyncio.Event()
    result: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    @strands_tool
    async def native_work(note: str) -> str:
        """Perform native work on a note."""
        entered.set()
        try:
            return await result
        finally:
            exited.set()

    recorder = ActivityRecorder()
    recorder.observing(tools)
    model = HeldFollowupModel(native_work.tool_name)
    adapter = StrandsAdapter(llm=model, additional_tools=[native_work], emit=())
    await adapter.on_started("Bot", "A bot")
    turn = asyncio.create_task(_run_message(adapter, tools))
    try:
        yield NativeActivityTurn(
            native_work.tool_name, entered, exited, result, recorder, turn, model
        )
    finally:
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
