"""A completed stop request cannot release an independently running backend."""

from __future__ import annotations

import pytest
from band_sdk_core import backend_stop_unconfirmed_message

from band.core.exceptions import InputNotAccepted
from band.core.turn_lifecycle import BackendTurns
from band.testing import FakeAgentTools
from band.testing.fake_tools import reported_failures
from tests.core.recordingcancel import RecordingCancel
from tests.runtime.helpers import ROOM_ID


class TestBackendStopConfirmation:
    async def test_terminal_confirmation_precedes_late_local_cancellation(self) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        handle = "backend-turn"
        async with turns.running(ROOM_ID, handle):
            turns.confirm_stopped(ROOM_ID, handle)
            turns.cancel(ROOM_ID)
        await turns.drain()
        await turns.require_admission(ROOM_ID, FakeAgentTools())

    async def test_failed_reporting_keeps_input_pending_and_retries_the_report(
        self,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        tools = FakeAgentTools()
        tools.send_event_error = RuntimeError("REST unavailable")
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        with pytest.raises(InputNotAccepted) as first:
            await turns.require_admission(ROOM_ID, tools)
        assert reported_failures(tools) == []
        tools.send_event_error = None
        with pytest.raises(InputNotAccepted) as second:
            await turns.require_admission(ROOM_ID, tools)
        assert first.value.ready is second.value.ready
        assert len(reported_failures(tools)) == 1

    async def test_completed_stop_holds_input_until_independent_confirmation(
        self,
    ) -> None:
        cancel = RecordingCancel()
        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)
        tools = FakeAgentTools()
        handle = "backend-turn"
        async with turns.running(ROOM_ID, handle):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        with pytest.raises(InputNotAccepted) as first:
            await turns.require_admission(ROOM_ID, tools)
        with pytest.raises(InputNotAccepted) as second:
            await turns.require_admission(ROOM_ID, tools)
        assert first.value.ready is second.value.ready
        assert reported_failures(tools) == [
            {
                "provider": "test",
                "code": None,
                "message": backend_stop_unconfirmed_message(),
                "detail": None,
            }
        ]
        turns.confirm_stopped(ROOM_ID, "another-turn")
        assert first.value.ready is not None and not first.value.ready.done()
        turns.confirm_stopped(ROOM_ID, handle)
        await first.value.ready
        await turns.require_admission(ROOM_ID, tools)

    async def test_unmigrated_backend_keeps_existing_stop_policy(self) -> None:
        cancel = RecordingCancel()
        turns = BackendTurns(cancel, provider="test")
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        await turns.require_admission(ROOM_ID, FakeAgentTools())
        assert cancel.finished == ["backend-turn"]
