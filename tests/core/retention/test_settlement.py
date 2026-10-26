"""Outcome settlement stays owned until its operation and acquisition finish."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import Residency
from tests.adapters.hold import Hold
from tests.core.retention.kit import ResidenceHost


class TestSettlement:
    async def test_disposal_joins_the_accepted_operation_without_capturing_its_caller(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        accepted = Hold()
        unrelated = Hold()
        continued = asyncio.Event()

        async def operation() -> None:
            async with resident.room.use():
                task = asyncio.current_task()
                assert task is not None
                resident.room.begin_settlement(task)
                await accepted.wait()

        async def caller() -> None:
            await resident.room.start_owned(operation)
            await unrelated.wait()
            continued.set()

        wrapper = asyncio.create_task(caller())
        try:
            await accepted.arrived()
            attempt = resident.room.request_disposal()
            assert attempt is not None
            assert resident.room.closing and resident.room.snapshot.accepted == 1
            assert resident.live and not attempt.done()

            accepted.released.set()
            await unrelated.arrived()
            await attempt
            assert resident.room.snapshot.state is Residency.CLOSED
            assert not resident.live and resident.identity is None
            assert not wrapper.done() and not wrapper.cancelling()

            unrelated.released.set()
            await wrapper
            assert continued.is_set()
        finally:
            accepted.released.set()
            unrelated.released.set()
            await asyncio.gather(wrapper, return_exceptions=True)

    @pytest.mark.parametrize("clear_cancel", [False, True])
    async def test_disposal_joins_an_abandoned_acquisition_without_recancelling(
        self, host: ResidenceHost, clear_cancel: bool
    ) -> None:
        resident = host.room()
        resident.resists_cancel = True
        resident.clear_cancel = clear_cancel
        resident.opening = Hold()
        operation = resident.room.start_owned(resident.turn)
        await resident.opening.arrived()
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await resident.resisted.wait()
        attempt = resident.room.request_disposal()
        assert attempt is not None
        # A queued observer runs after the disposal has reached acquisition join.
        observed = asyncio.Event()
        asyncio.get_running_loop().call_soon(observed.set)
        await observed.wait()
        assert resident.live and resident.releases == []
        assert resident.cancellations == 1 and not attempt.done()
        resident.opening.released.set()
        await attempt
        assert resident.cancellations == 1
        assert not resident.live and resident.identity is None
        assert resident.room.snapshot.state is Residency.CLOSED
        replacement = host.room()
        await replacement.turn()
        assert replacement.live

    async def test_settlement_keeps_the_lease_until_outcome_handling_finishes(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        settling = Hold()

        async def operation() -> None:
            async with resident.room.use():
                task = asyncio.current_task()
                assert task is not None
                resident.room.begin_settlement(task)
                await settling.wait()

        task = resident.room.start_owned(operation)
        await settling.arrived()
        attempt = resident.room.request_disposal()
        assert attempt is not None
        assert resident.room.closing and resident.room.snapshot.leases == 1
        assert not task.cancelling() and resident.live
        settling.released.set()
        await task
        await attempt
        assert not task.cancelled() and not resident.live

    async def test_settlement_rejects_unenrolled_work(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        task = asyncio.current_task()
        assert task is not None
        with pytest.raises(ValueError, match="enrolled"):
            resident.room.begin_settlement(task)
