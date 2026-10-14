"""Two retained rooms through idle release, a failed wake and final disposal."""

from __future__ import annotations

import pytest

from band.core.retention import ProviderActivity, ReleaseCause, Residency
from tests.core.retention.kit import ResidenceHost


class TestRetentionLifecycle:
    async def test_an_idle_room_cycles_while_a_busy_room_stays_resident(
        self, residences: ResidenceHost
    ) -> None:
        idle = residences.room("idle")
        busy = residences.room("busy")
        await idle.turn()
        await busy.turn()
        busy.observe(ProviderActivity.ACTIVE, "background job")

        await residences.clock.advance(10)
        await idle.suspended.get()
        assert (idle.live, busy.live) == (False, True)

        idle.fail_acquire = True
        with pytest.raises(RuntimeError, match="resume rejected"):
            await idle.turn()
        idle.fail_acquire = False
        await idle.turn()
        assert idle.live and idle.identity == "native-1"
        assert idle.resume_requests == [False, True, True]

        await residences.owner.stop()
        assert idle.releases == [
            ReleaseCause.IDLE,
            ReleaseCause.RECOVERY,
            ReleaseCause.FINAL,
        ]
        assert busy.releases == [ReleaseCause.FINAL]
        assert (idle.identity, busy.identity) == (None, None)
        assert {idle.room.snapshot.state, busy.room.snapshot.state} == {
            Residency.CLOSED
        }
