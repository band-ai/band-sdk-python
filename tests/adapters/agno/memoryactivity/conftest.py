"""Run memory events through the adapter with confirmed task cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import cast
from unittest.mock import MagicMock

import pytest_asyncio

from band.adapters.agno import AgnoAdapter
from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.memoryactivity.peer import MemoryStream, MemoryTurn


@pytest_asyncio.fixture(loop_scope="function")
async def memory_turn(
    make_agno_agent: Callable[..., MagicMock],
    tools: FakeAgentTools,
    sample_platform_message: PlatformMessage,
) -> AsyncIterator[MemoryTurn]:
    peer = MemoryStream()
    agent = make_agno_agent(update_memory_on_run=True)
    agent.arun = peer.arun
    adapter = AgnoAdapter(agent=agent, emit=())
    await adapter.on_started("Bot", "A bot")
    recorder = ActivityRecorder()
    recorder.observing(tools)
    turn = asyncio.create_task(
        adapter.on_turn(
            [sample_platform_message],
            cast(AgentToolsProtocol, tools),
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id=sample_platform_message.room_id,
        )
    )
    try:
        yield MemoryTurn(peer, recorder, turn)
    finally:
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        await adapter.on_cleanup(sample_platform_message.room_id)
