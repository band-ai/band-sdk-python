"""CapturedWorkerOrigin behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from band.core.turn_lifecycle import bind_turn_task
from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from band.runtime.cycle import TurnScope
from band.runtime.tools.agent import AgentTools


class TestCapturedWorkerOrigin:
    async def test_tools_replacement_does_not_rebind_an_accepted_worker(self) -> None:
        original = AgentTools(room_id="room", rest=MagicMock(), participants=[])
        original._scope = scope = TurnScope(control_revision=0)
        replacement = AgentTools(room_id="room", rest=MagicMock(), participants=[])
        replacement._scope = TurnScope(control_revision=1)
        wrapper = DedupingAgentTools(original)
        started = asyncio.Event()

        async def work() -> None:
            started.set()
            await asyncio.Future[None]()

        owner = bind_turn_task(wrapper, asyncio.create_task(work()))
        await started.wait()
        wrapper = wrapper.for_origin(replacement)
        scope.terminate()
        assert await owner.drain()
        assert owner.task.cancelled()
        assert not replacement._scope.aborted
        with pytest.raises(asyncio.CancelledError):
            await original.send_event("late reply", "thought")
