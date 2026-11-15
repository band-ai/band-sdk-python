"""Stop recovery retains ownership; failed recovery makes the room unavailable."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import InputNotAccepted, ProviderUnavailableError
from band.core.turn_lifecycle import PROVIDER_UNAVAILABLE_MESSAGE, BackendTurns
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.hold import Hold
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

    async def test_failed_release_makes_the_room_unavailable_until_confirmed(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        turns = BackendTurns(RecordingCancel(), provider="test")
        tools = FakeAgentTools()
        handle = "operation"
        turns.recovering(
            "room",
            handle,
            resident.room,
            generation=resident.room.snapshot.generation,
        )
        with pytest.raises(ProviderUnavailableError) as first:
            await turns.require_admission("room", tools)
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission("room", tools)
        assert first.value.failure.message == PROVIDER_UNAVAILABLE_MESSAGE
        assert turns.unavailable("room")
        assert turns.blocker("room") is None
        assert resident.live
        assert resident.resume_requests == [False]
        assert failure_reports(tools) == [("test", PROVIDER_UNAVAILABLE_MESSAGE)]

        resident.fail_close = False
        await resident.room.wait_released(generation=resident.room.generation)
        assert not resident.live
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission("room", tools)
        turns.confirm_stopped("room", handle)
        await turns.require_admission("room", tools)
        await resident.turn()
        assert resident.resume_requests == [False, False]

    async def test_held_release_defers_until_evidence_then_admits(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Hold()
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        tools = FakeAgentTools()
        handle = "operation"
        turns.recovering(
            "room",
            handle,
            resident.room,
            generation=resident.room.generation,
        )
        attempt = asyncio.create_task(turns.require_admission("room", tools))
        await resident.closing.arrived()
        assert not attempt.done()
        assert failure_reports(tools) == []
        resident.closing.released.set()
        await attempt
        assert not resident.live
        assert turns.blocker("room") is None
