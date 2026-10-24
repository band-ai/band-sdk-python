"""Request-only disposal exposes the existing shared attempt and retry."""

from __future__ import annotations

import pytest

from band.core.exceptions import RoomClosingError
from tests.adapters.hold import Hold
from tests.core.retention.kit import ResidenceHost


class TestDisposalRequest:
    async def test_request_closes_admission_and_shares_the_attempt(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.closing = Hold()
        attempt = resident.room.request_disposal()
        assert attempt is not None
        assert resident.room.request_disposal() is attempt
        with pytest.raises(RoomClosingError):
            await resident.turn()
        await resident.closing.arrived()
        assert resident.live and not attempt.done()
        resident.closing.released.set()
        await attempt
        assert resident.room.request_disposal() is None
        assert not resident.live

    async def test_a_later_request_retries_a_settled_failure(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        attempt = resident.room.request_disposal()
        assert attempt is not None
        with pytest.raises(RuntimeError, match="close refused"):
            await attempt
        assert resident.live
        resident.fail_close = False
        retry = resident.room.request_disposal()
        assert retry is not None and retry is not attempt
        await retry
        assert not resident.live

    async def test_an_enrolled_requester_can_finish_without_self_joining(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        finishing = Hold()

        async def operation() -> None:
            async with resident.room.use():
                assert resident.room.request_disposal() is not None
                await finishing.wait()

        task = resident.room.start_owned(operation)
        await finishing.arrived()
        attempt = resident.room.request_disposal()
        assert attempt is not None
        assert not task.cancelling() and resident.live
        finishing.released.set()
        await task
        await attempt
        assert not task.cancelled() and not resident.live
