"""Unavailable rooms latch terminal delivery failure without indefinite deferral."""

from __future__ import annotations

import asyncio
import gc
import weakref

import pytest
from band_sdk_core import backend_stop_unconfirmed_message

from band.core.exceptions import (
    CleanupIncompleteError,
    InputNotAccepted,
    ProviderUnavailableError,
)
from band.core.turn_lifecycle import (
    PROVIDER_UNAVAILABLE_MESSAGE,
    BackendTurns,
)
from band.testing import FakeAgentTools, failure_reports
from tests.core.latch import RecordingLatch
from tests.core.recordingcancel import RecordingCancel
from tests.core.retention.kit import ResidenceHost
from tests.runtime.helpers import ROOM_ID


class TestBackendUnavailable:
    async def test_failed_recovery_marks_unavailable_and_fails_fast(self) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            await resident.turn()
            resident.fail_close = True
            turns = BackendTurns(
                RecordingCancel(), provider="test", requires_confirmation=True
            )
            tools = FakeAgentTools()
            latched: list[frozenset[str]] = []
            turns.add_failure_latch(
                lambda room_id, ids: latched.append(ids) if room_id == "room" else None
            )
            turns.recovering(
                "room",
                "operation",
                resident.room,
                generation=resident.room.snapshot.generation,
            )
            turns.note_waiting("room", "msg-a")
            turns.note_waiting("room", "msg-b")
            with pytest.raises(ProviderUnavailableError) as first:
                await turns.require_admission("room", tools)
            assert first.value.failure.message == PROVIDER_UNAVAILABLE_MESSAGE
            assert turns.unavailable("room")
            assert latched == [frozenset({"msg-a", "msg-b"})]
            with pytest.raises(ProviderUnavailableError):
                await turns.require_admission("room", tools)
            assert failure_reports(tools) == [
                ("test", PROVIDER_UNAVAILABLE_MESSAGE),
            ]

    async def test_successful_release_after_failed_recovery_reopens_admission(
        self,
    ) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            await resident.turn()
            resident.fail_close = True
            turns = BackendTurns(
                RecordingCancel(), provider="test", requires_confirmation=True
            )
            tools = FakeAgentTools()
            handle = "operation"
            generation = resident.room.snapshot.generation
            turns.recovering(
                "room",
                handle,
                resident.room,
                generation=generation,
            )
            with pytest.raises(ProviderUnavailableError):
                await turns.require_admission("room", tools)
            assert turns.unavailable("room")
            assert failure_reports(tools) == [("test", PROVIDER_UNAVAILABLE_MESSAGE)]
            # Physical release still pending: sticky unavailable refuses again.
            with pytest.raises(ProviderUnavailableError):
                await turns.require_admission("room", FakeAgentTools())
            resident.fail_close = False
            await resident.room.wait_released(generation=generation)
            assert not resident.live
            assert not turns.unavailable("room")
            await turns.require_admission("room", FakeAgentTools())

    async def test_stale_confirmation_cannot_reopen_a_newer_unavailable_stop(
        self,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        tools = FakeAgentTools()
        async with turns.running(ROOM_ID, "old"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        turns.mark_unavailable(ROOM_ID)
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission(ROOM_ID, tools)
        turns.confirm_stopped(ROOM_ID, "old")
        async with turns.running(ROOM_ID, "new"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        turns.mark_unavailable(ROOM_ID)
        turns.confirm_stopped(ROOM_ID, "old")
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission(ROOM_ID, tools)
        turns.confirm_stopped(ROOM_ID, "new")
        await turns.require_admission(ROOM_ID, tools)

    async def test_unconfirmed_stop_still_reports_once_while_waiting(self) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        tools = FakeAgentTools()
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        with pytest.raises(InputNotAccepted):
            await turns.require_admission(ROOM_ID, tools)
        assert failure_reports(tools) == [
            ("test", backend_stop_unconfirmed_message()),
        ]

    async def test_mark_unavailable_wakes_readiness_after_latching(self) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        ready = turns.blocker(ROOM_ID)
        assert ready is not None and not ready.done()
        turns.note_waiting(ROOM_ID, "queued-1")
        latched: list[frozenset[str]] = []
        turns.add_failure_latch(lambda _room, ids: latched.append(ids))
        turns.mark_unavailable(ROOM_ID)
        assert latched == [frozenset({"queued-1"})]
        assert ready.done()
        assert turns.blocker(ROOM_ID) is None

    async def test_waiting_after_unavailable_latches_immediately(self) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        ready = turns.blocker(ROOM_ID)
        assert ready is not None
        latched: list[frozenset[str]] = []
        turns.add_failure_latch(lambda _room, ids: latched.append(ids))
        turns.mark_unavailable(ROOM_ID)
        assert latched == [frozenset()]
        turns.note_waiting(ROOM_ID, "late-deferred")
        turns.note_waiting_on(ready, "via-ready")
        assert latched == [
            frozenset(),
            frozenset({"late-deferred"}),
            frozenset({"via-ready"}),
        ]

    async def test_unavailable_report_is_not_suppressed_by_a_prior_blocked_report(
        self,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        tools = FakeAgentTools()
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        with pytest.raises(InputNotAccepted):
            await turns.require_admission(ROOM_ID, tools)
        turns.mark_unavailable(ROOM_ID)
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission(ROOM_ID, tools)
        assert failure_reports(tools) == [
            ("test", backend_stop_unconfirmed_message()),
            ("test", PROVIDER_UNAVAILABLE_MESSAGE),
        ]

    async def test_release_typed_as_cleanup_incomplete_latches_unavailable(
        self,
    ) -> None:
        async with ResidenceHost() as host:
            resident = host.room()
            await resident.turn()
            turns = BackendTurns(
                RecordingCancel(), provider="test", requires_confirmation=True
            )
            tools = FakeAgentTools()
            resident.fail_close = True
            resident.close_error = CleanupIncompleteError("Worker has not stopped")
            turns.recovering(
                "room", "operation", resident.room, generation=resident.room.generation
            )
            with pytest.raises(ProviderUnavailableError):
                await turns.require_admission("room", tools)
            assert turns.unavailable("room")
            assert failure_reports(tools) == [("test", PROVIDER_UNAVAILABLE_MESSAGE)]

    async def test_deferred_originals_on_the_stopping_task_latch_on_unavailable(
        self,
    ) -> None:
        hold = asyncio.Event()

        async def slow_cancel(_handle: object) -> None:
            await hold.wait()

        turns = BackendTurns(slow_cancel, provider="test", requires_confirmation=True)
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        blocker = turns.blocker(ROOM_ID)
        assert blocker is not None and turns.stopping(ROOM_ID)
        turns.note_waiting_on(blocker, "msg-during-stop")
        latched: list[frozenset[str]] = []
        turns.add_failure_latch(lambda _room, ids: latched.append(ids))
        hold.set()
        await turns.settled(ROOM_ID)
        turns.mark_unavailable(ROOM_ID)
        assert latched == [frozenset({"msg-during-stop"})]

    async def test_running_refuses_a_room_already_latched_unavailable(self) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        turns.mark_unavailable(ROOM_ID)
        with pytest.raises(ProviderUnavailableError):
            async with turns.running(ROOM_ID, "next"):
                pass

    async def test_running_refuses_an_unconfirmed_stop_without_hanging(self) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        with pytest.raises(InputNotAccepted) as refused:
            async with turns.running(ROOM_ID, "next"):
                pass
        assert refused.value.ready is turns.blocker(ROOM_ID)
        assert not refused.value.ready.done()

    async def test_latch_of_a_retired_room_is_not_retained_by_the_backend_owner(
        self,
    ) -> None:
        turns = BackendTurns(
            RecordingCancel(), provider="test", requires_confirmation=True
        )
        live, retired = RecordingLatch(), RecordingLatch()
        turns.add_failure_latch(live.latch)
        turns.add_failure_latch(retired.latch)
        gone = weakref.ref(retired)
        del retired
        gc.collect()
        assert gone() is None

        async with turns.running(ROOM_ID, "backend-turn"):
            turns.cancel(ROOM_ID)
        await turns.settled(ROOM_ID)
        turns.note_waiting(ROOM_ID, "queued-1")
        turns.mark_unavailable(ROOM_ID)
        assert live.latched == [frozenset({"queued-1"})]
