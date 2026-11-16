"""Task-identity gates abandon waiting only; provider TimeoutError does not."""

from __future__ import annotations

import asyncio

import pytest

from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.integrations.a2a.adapter import A2AAdapter
from band.integrations.a2a.types import A2AAdapterConfig
from band.testing import FakeAgentTools, failure_reports
from tests.framework_conformance.stopprobes import cancel_turn
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.integrations.a2a.kit import (
    CANCELLED,
    WORKING,
    RecordingA2AClient,
    task_update,
)


class TestTaskRevealBudget:
    @pytest.mark.asyncio
    @pytest.mark.looptime
    async def test_reveal_budget_cancels_a_late_task_without_unavailable(self) -> None:
        adapter = A2AAdapter(
            A2AAdapterConfig(remote_url="http://a2a.test", transition_timeout_s=0.05)
        )
        client = RecordingA2AClient()
        adapter._client = client
        gate = asyncio.Event()
        client.script(task_update("task-1", WORKING), gate=gate)

        turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
        try:
            with pytest.raises(TimeoutError, match="A2A task reveal"):
                await turn
            assert not adapter.backend_turns.unavailable(ROOM_ID)
            assert client.cancelled == []

            gate.set()
            await adapter.backend_turns.drain()

            assert client.cancelled == [f"{CANCELLED} task-1"]
            assert not adapter.backend_turns.unavailable(ROOM_ID)
            await adapter.backend_turns.require_admission(ROOM_ID, FakeAgentTools())
        finally:
            if not turn.done():
                await cancel_turn(turn)
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_provider_timeout_on_reveal_does_not_enroll_late_cancel(
        self,
    ) -> None:
        adapter = A2AAdapter(A2AAdapterConfig(remote_url="http://a2a.test"))
        client = RecordingA2AClient()
        adapter._client = client
        client.script(error=TimeoutError("SSE read timed out"))
        tools = FakeAgentTools()

        with pytest.raises(TimeoutError, match="SSE read timed out"):
            await adapter.on_event(turn_input(tools))

        assert client.cancelled == []
        assert not adapter.backend_turns.unavailable(ROOM_ID)
        assert not adapter.backend_turns.stopping(ROOM_ID)
        assert failure_reports(tools) == [("a2a", GENERIC_PROVIDER_FAILURE_MESSAGE)]
        await adapter.backend_turns.require_admission(ROOM_ID, FakeAgentTools())
        await adapter.cleanup_all()
