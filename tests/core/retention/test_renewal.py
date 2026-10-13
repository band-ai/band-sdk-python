"""A restart renews a stopped owner only once its stop confirmed every room
closed, keeping its settings."""

from __future__ import annotations

import pytest

from band.core.exceptions import CleanupIncompleteError
from tests.core.retention.kit import ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestRenewal:
    async def test_an_owner_that_never_stopped_is_kept(
        self, host: ResidenceHost
    ) -> None:
        assert host.owner.renewed() is host.owner

    async def test_a_confirmed_stop_renews_with_the_same_settings(
        self, graceful_host: ResidenceHost
    ) -> None:
        stopped = graceful_host.owner
        await graceful_host.room().turn()
        await stopped.stop()

        renewed = stopped.renewed()

        assert renewed is not stopped and not renewed.stopping
        assert (renewed.config, renewed.clock, renewed.settle_grace_s) == (
            stopped.config,
            stopped.clock,
            stopped.settle_grace_s,
        )

    async def test_a_failed_stop_refuses_renewal(self, host: ResidenceHost) -> None:
        resident = host.room()
        await resident.turn()
        resident.fail_close = True
        with pytest.raises(BaseExceptionGroup):
            await host.owner.stop()

        with pytest.raises(CleanupIncompleteError):
            host.owner.renewed()
