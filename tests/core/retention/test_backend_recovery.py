"""Stop recovery retains unaccepted input until the existing release owner succeeds."""

from __future__ import annotations

import pytest
from band_sdk_core import backend_stop_unconfirmed_message

from band.core.exceptions import InputNotAccepted
from band.core.turn_lifecycle import BackendTurns
from band.testing import FakeAgentTools, failure_reports
from tests.core.recordingcancel import RecordingCancel
from tests.core.retention.kit import ResidenceHost


class TestBackendRecovery:
    async def test_closing_room_refuses_without_a_reusable_readiness_signal(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        turns = BackendTurns(RecordingCancel(), provider="test")
        turns.recovering(
            "room", "operation", resident.room, generation=resident.room.generation
        )
        disposal = resident.room.request_disposal()
        assert disposal is not None
        try:
            with pytest.raises(InputNotAccepted) as refusal:
                await turns.require_admission("room", FakeAgentTools())
            assert refusal.value.ready is None
        finally:
            await disposal

    async def test_completed_disposal_clears_its_stale_recovery_record(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        turns = BackendTurns(RecordingCancel(), provider="test")
        turns.recovering(
            "room", "operation", resident.room, generation=resident.room.generation
        )
        blocker = turns.blocker("room")
        assert blocker is not None
        await resident.room.dispose()
        turns.confirm_disposed("room")
        assert blocker.done()
        assert turns.blocker("room") is None

        replacement = host.room()
        await replacement.turn()
        turns.recovering(
            "room",
            "replacement",
            replacement.room,
            generation=replacement.room.generation,
        )
        current = turns.blocker("room")
        turns.confirm_disposed("room")
        assert turns.blocker("room") is current
        assert current is not None and not current.done()

    async def test_failed_release_reports_once_then_safe_recovery_allows_admission(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        turns = BackendTurns(RecordingCancel(), provider="test")
        tools = FakeAgentTools()
        turns.recovering(
            "room",
            "operation",
            resident.room,
            generation=resident.room.snapshot.generation,
        )
        with pytest.raises(InputNotAccepted) as first:
            await turns.require_admission("room", tools)
        with pytest.raises(InputNotAccepted) as second:
            await turns.require_admission("room", tools)
        turns.confirm_disposed("room")
        assert first.value.ready is second.value.ready
        assert turns.blocker("room") is first.value.ready
        assert resident.live
        assert resident.resume_requests == [False]
        assert failure_reports(tools) == [("test", backend_stop_unconfirmed_message())]

        resident.fail_close = False
        await turns.require_admission("room", tools)
        assert first.value.ready is not None and first.value.ready.done()
        assert not resident.live
        await resident.turn()
        assert resident.resume_requests == [False, False]
