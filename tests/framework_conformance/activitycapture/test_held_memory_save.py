"""A held memory save reaches the platform only after the test releases it."""

from __future__ import annotations

import asyncio

import pytest

from band.client.rest import AgentMemoryCreateRequest
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from tests.e2e.baseline.toolkit.heldmemory import HeldMemorySave
from tests.runtime.helpers import WAIT_SECONDS, memory_client

MEMORY = AgentMemoryCreateRequest(
    content="remember this",
    system=MemorySystem.LONG_TERM,
    type=MemoryType.SEMANTIC,
    segment=MemorySegment.USER,
    thought="test",
    scope=MemoryStoreScope.AGENT,
)


class TestHeldMemorySave:
    async def test_save_is_sent_only_after_release(self) -> None:
        held = HeldMemorySave(deadline_s=WAIT_SECONDS)
        async with memory_client() as (rest, requests), held.releasing_on_exit():
            held.hold(rest)
            save = asyncio.create_task(
                rest.agent_api_memories.create_agent_memory(memory=MEMORY)
            )
            await held.wait(held.save.entered, phase="entered")
            assert requests == []

            held.save.release.set()
            await save
            await held.wait(held.save.settled, phase="settled")
            assert [(r.method, r.url.path) for r in requests] == [
                ("POST", "/api/v1/agent/memories")
            ]

    async def test_second_save_fails_without_reaching_the_platform(self) -> None:
        held = HeldMemorySave(deadline_s=WAIT_SECONDS)
        async with memory_client() as (rest, requests), held.releasing_on_exit():
            held.hold(rest)
            held.save.release.set()
            await rest.agent_api_memories.create_agent_memory(memory=MEMORY)

            with pytest.raises(RuntimeError, match="accepts one request"):
                await rest.agent_api_memories.create_agent_memory(memory=MEMORY)
            assert len(requests) == 1
