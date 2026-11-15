"""Physical release wakes refused admission after its bounded wait expires."""

from __future__ import annotations

import gc
import weakref

import pytest

from band.core.exceptions import InputNotAccepted
from band.core.retention import Residency
from band.core.turn_lifecycle import BackendTurns
from band.testing import FakeAgentTools
from tests.adapters.hold import Hold
from tests.core.recordingcancel import RecordingCancel
from tests.core.retention.kit import IMPATIENT_TRANSITION_S, ResidenceHost
from tests.runtime.deferral.kit import callbacks_finished
from tests.toolkit.tasks import background


@pytest.mark.looptime
class TestLateRecovery:
    async def test_release_after_admission_timeout_settles_readiness(self) -> None:
        async with ResidenceHost(
            timeout=None, transition_timeout=IMPATIENT_TRANSITION_S
        ) as host:
            resident = host.room()
            await resident.turn()
            resident.closing = Hold()
            turns = BackendTurns(RecordingCancel(), provider="test")
            generation = resident.room.generation
            turns.recovering("room", object(), resident.room, generation=generation)
            await resident.closing.arrived()
            with pytest.raises(InputNotAccepted) as refused:
                await turns.require_admission("room", FakeAgentTools())
            ready = refused.value.ready
            assert ready is not None and not ready.done()
            assert resident.live

            resident.closing.released.set()
            await resident.room.wait_released(generation=generation)
            assert resident.room.snapshot.state is Residency.SUSPENDED
            assert not resident.live
            assert ready.done()
            assert turns.blocker("room") is None

    async def test_failed_release_stays_blocked_until_successful_retry(self) -> None:
        async with ResidenceHost(timeout=None) as host:
            resident = host.room()
            await resident.turn()
            resident.fail_close = True
            turns = BackendTurns(RecordingCancel(), provider="test")
            generation = resident.room.generation
            turns.recovering("room", object(), resident.room, generation=generation)
            with pytest.raises(InputNotAccepted) as refused:
                await turns.require_admission("room", FakeAgentTools())
            ready = refused.value.ready
            assert ready is not None and not ready.done()
            assert resident.live

            resident.fail_close = False
            await resident.room.wait_released(generation=generation)
            assert not resident.live
            assert ready.done()
            assert turns.blocker("room") is None

    async def test_queued_old_release_cannot_confirm_a_replacement_stop(self) -> None:
        async with ResidenceHost(timeout=None) as host:
            resident = host.room()
            await resident.turn()
            generation = resident.room.generation
            resident.room.recover(generation=generation, reason="retire")
            await resident.room.wait_released(generation=generation)
            await resident.turn()
            turns = BackendTurns(RecordingCancel(), provider="test")
            handle = object()
            turns.recovering("room", handle, resident.room, generation=generation)
            turns.confirm_stopped("room", handle)
            resident.closing = Hold()
            turns.recovering(
                "room", handle, resident.room, generation=resident.room.generation
            )
            ready = turns.blocker("room")
            await callbacks_finished()
            await resident.closing.arrived()
            assert ready is not None and not ready.done()
            assert resident.live
            resident.closing.released.set()
            await resident.room.wait_released(generation=resident.room.generation)
            assert ready.done()

    async def test_explicit_confirmation_detaches_the_release_observer(self) -> None:
        async with ResidenceHost(timeout=None) as host:
            resident = host.room()
            await resident.turn()
            resident.closing = Hold()
            turns = BackendTurns(RecordingCancel(), provider="test")
            handle = object()
            turns.recovering(
                "room", handle, resident.room, generation=resident.room.generation
            )
            await resident.closing.arrived()
            turns.confirm_stopped("room", handle)
            retired = weakref.ref(turns)
            del turns
            gc.collect()
            assert retired() is None
            assert resident.live

    async def test_queued_old_generation_cannot_confirm_reregistered_recovery(
        self,
    ) -> None:
        async with ResidenceHost(timeout=None) as host:
            resident = host.room()
            await resident.turn()
            generation = resident.room.generation
            resident.room.recover(generation=generation, reason="retire")
            await resident.room.wait_released(generation=generation)
            await resident.turn()
            turns = BackendTurns(RecordingCancel(), provider="test")
            handle = object()
            turns.recovering("room", handle, resident.room, generation=generation)
            ready = turns.blocker("room")
            resident.closing = Hold()
            turns.recovering(
                "room", handle, resident.room, generation=resident.room.generation
            )
            assert turns.blocker("room") is ready
            await callbacks_finished()
            await resident.closing.arrived()
            assert ready is not None and not ready.done()
            assert resident.live
            resident.closing.released.set()
            await resident.room.wait_released(generation=resident.room.generation)
            assert ready.done()

    async def test_disposal_finishes_observation_registered_during_dispose(
        self,
    ) -> None:
        async with ResidenceHost(timeout=None) as host:
            resident = host.room()
            await resident.turn()
            resident.leaving = Hold()
            async with background(resident.room.dispose()) as disposal:
                await resident.leaving.arrived()
                turns = BackendTurns(RecordingCancel(), provider="test")
                turns.recovering(
                    "room", object(), resident.room, generation=resident.room.generation
                )
                ready = turns.blocker("room")
                assert ready is not None and not ready.done()
                resident.leaving.released.set()
                await disposal
                assert ready.done()
                assert turns.blocker("room") is None
                assert resident.room.snapshot.state is Residency.CLOSED
