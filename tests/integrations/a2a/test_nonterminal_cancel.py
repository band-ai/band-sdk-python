"""Unproven task settlement remains unavailable across room leave and rejoin."""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import StreamResponse, TaskState

from band.client.streaming import ControlMode
from band.core.exceptions import ProviderUnavailableError
from band.core.turn_lifecycle import PROVIDER_UNAVAILABLE_MESSAGE
from band.integrations.a2a.adapter import A2AAdapter
from band.integrations.a2a.types import A2AAdapterConfig
from band.testing import FakeAgentTools, failure_reports
from tests.framework_conformance.stopprobes import cancel_turn
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.integrations.a2a.kit import (
    CANCELED,
    WORKING,
    RecordingA2AClient,
    task_update,
)


class TestNonterminalCancel:
    @pytest.mark.parametrize(
        "cancel_state,response_id,extra_events",
        [
            pytest.param(TaskState.TASK_STATE_WORKING, None, (), id="nonterminal"),
            pytest.param(
                TaskState.TASK_STATE_CANCELED, "other-task", (), id="wrong-cancel-task"
            ),
            pytest.param(
                TaskState.TASK_STATE_WORKING,
                None,
                (task_update("other-task", CANCELED),),
                id="wrong-stream-task",
            ),
        ],
    )
    async def test_unproven_settlement_cannot_be_reset_by_leaving(
        self,
        cancel_state: TaskState.ValueType,
        response_id: str | None,
        extra_events: tuple[StreamResponse, ...],
    ) -> None:
        adapter = A2AAdapter(A2AAdapterConfig(remote_url="http://a2a.test"))
        client = RecordingA2AClient()
        client.cancel_state = cancel_state
        client.cancel_response_id = response_id
        adapter._client = client
        # Mismatched identities deliberately inject invalid remote evidence.
        client.script(task_update("task-1", WORKING), *extra_events, hold=True)
        turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
        try:
            await client.stream_held.wait()
            await cancel_turn(turn)
            await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
            await adapter.backend_turns.drain()
            tools = FakeAgentTools()
            with pytest.raises(ProviderUnavailableError):
                await adapter.on_event(turn_input(tools))
            await adapter.on_cleanup(ROOM_ID)
            with pytest.raises(ProviderUnavailableError):
                await adapter.on_event(turn_input(tools))
            # Leave/rejoin never re-invokes the remote, and the refusal is sticky.
            assert client.continued == [""]
            assert len(client.cancelled) == 1
            assert failure_reports(tools) == [("a2a", PROVIDER_UNAVAILABLE_MESSAGE)]
            assert adapter.backend_turns.unavailable(ROOM_ID)
        finally:
            await cancel_turn(turn)
            await adapter.cleanup_all()
