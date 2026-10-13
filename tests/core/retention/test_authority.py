"""Only the current residency's events may change a room."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import (
    ProviderActivity,
)
from tests.core.retention.kit import ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestAuthority:
    """Only the current residency's events may change a room."""

    async def test_old_process_events_cannot_unpin_replacement(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        old_generation = resident.room.snapshot.generation
        await host.clock.advance(10)
        await resident.suspended.get()
        await resident.turn()
        resident.observe(ProviderActivity.ACTIVE)
        assert not resident.room.observe(
            ProviderActivity.QUIESCENT, generation=old_generation
        )
        await host.clock.advance(10)
        assert resident.live

    async def test_stale_holder_events_cannot_mutate_a_rejoined_room(
        self,
        host: ResidenceHost,
    ) -> None:
        old = host.room()
        await old.turn()
        await old.room.dispose()
        new = host.room()
        await new.turn()
        new.observe(ProviderActivity.ACTIVE)
        assert old.room.snapshot.generation == new.room.snapshot.generation
        assert not old.room.observe(ProviderActivity.QUIESCENT, generation=1)
        assert not old.room.recover(generation=1, reason="stale")
        assert new.room.snapshot.provider_activity is ProviderActivity.ACTIVE
        assert not new.room.snapshot.pending_cleanup

    async def test_a_stopping_owner_revokes_authority_before_disposal_starts(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()

        stopping = asyncio.create_task(host.owner.stop())
        await asyncio.sleep(0)  # stop() ran; its shutdown task has not
        assert host.owner.stopping
        assert resident.room.snapshot.pending_release is None
        assert not resident.room.has_authority(resident.room.generation)
        await stopping
