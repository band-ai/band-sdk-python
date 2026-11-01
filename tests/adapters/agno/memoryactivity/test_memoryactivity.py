"""Memory stream activity follows matching events and closes on interruption."""

from __future__ import annotations

import asyncio

import pytest
from agno.run.agent import MemoryUpdateCompletedEvent, MemoryUpdateStartedEvent

from band.testing.activity import SHOWN_MEMORY, SHOWN_REASONING
from tests.adapters.agno.memoryactivity.peer import MemoryTurn


class TestMemoryActivity:
    async def test_only_matching_completion_ends_memory(
        self, memory_turn: MemoryTurn
    ) -> None:
        await memory_turn.peer.events.put(MemoryUpdateStartedEvent(run_id="current"))
        await memory_turn.peer.consumed.get()
        assert memory_turn.recorder.events == [SHOWN_MEMORY]

        await memory_turn.peer.events.put(MemoryUpdateCompletedEvent(run_id="foreign"))
        await memory_turn.peer.consumed.get()
        assert memory_turn.recorder.events == [SHOWN_MEMORY]

        await memory_turn.peer.events.put(MemoryUpdateCompletedEvent(run_id="current"))
        await memory_turn.peer.consumed.get()
        assert memory_turn.recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]

    async def test_stream_failure_ends_unfinished_memory(
        self, memory_turn: MemoryTurn
    ) -> None:
        await memory_turn.peer.events.put(MemoryUpdateStartedEvent(run_id="current"))
        await memory_turn.peer.consumed.get()
        assert memory_turn.recorder.events == [SHOWN_MEMORY]

        await memory_turn.peer.events.put(ValueError("stream failed"))
        with pytest.raises(ValueError, match="stream failed"):
            await memory_turn.task
        assert memory_turn.recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]

    async def test_cancellation_ends_unfinished_memory(
        self, memory_turn: MemoryTurn
    ) -> None:
        await memory_turn.peer.events.put(MemoryUpdateStartedEvent(run_id="current"))
        await memory_turn.peer.consumed.get()
        assert memory_turn.recorder.events == [SHOWN_MEMORY]

        memory_turn.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await memory_turn.task
        assert memory_turn.recorder.events == [SHOWN_MEMORY, SHOWN_REASONING]
