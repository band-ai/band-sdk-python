"""Fixtures: the real runtime over a stateful room peer with the cycle watchdog armed."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from band.agent import Agent
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from tests.platform.settlement.conftest import instant_retries
from tests.platform.settlement.kit import link_over
from tests.runtime.recovery.kit import (
    AGENT_ID,
    Harness,
    RoomPeer,
    ScriptedAdapter,
    Shape,
)
from tests.runtime.watchdog.kit import CYCLE_SECONDS

# Re-exported so one fixture owns the generated client's retry timing.
__all__ = ["instant_retries"]


@pytest.fixture
def room_peer() -> RoomPeer:
    return RoomPeer()


@pytest.fixture
def adapter() -> ScriptedAdapter:
    """A model that never finishes its turn."""
    scripted = ScriptedAdapter()
    scripted.gate = asyncio.Event()
    return scripted


@pytest_asyncio.fixture(loop_scope="function")
async def harness(
    room_peer: RoomPeer, adapter: ScriptedAdapter
) -> AsyncIterator[Harness]:
    """A batching runtime whose cycles are cut off after ``CYCLE_SECONDS``."""
    async with room_peer.client() as rest:
        agent = Agent(runtime=MagicMock(agent_id=AGENT_ID), adapter=adapter)
        runtime = AgentRuntime(
            link=link_over(rest),
            agent_id=AGENT_ID,
            on_execute=agent._on_execute,
            session_config=SessionConfig(
                consume_all_queued=True,
                enable_context_hydration=False,
                enable_working_state=False,
                max_cycle_seconds=CYCLE_SECONDS,
            ),
            on_execute_batch=agent._on_execute_batch,
            on_message_control=agent._on_control,
            is_message_control=agent._is_control,
        )
        try:
            yield Harness(
                peer=room_peer, adapter=adapter, runtime=runtime, shape=Shape.BATCH
            )
        finally:
            await runtime.stop()
