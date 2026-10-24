"""The watchdog budget and the recovery that follows its expiry."""

from __future__ import annotations

from tests.platform.settlement.kit import ROOM_ID
from tests.runtime.recovery.kit import (
    RESYNC_SECONDS,
    Entrance,
    Fault,
    Harness,
)

CYCLE_SECONDS = 30.0


async def run_past_the_watchdog(
    harness: Harness, *, outage: Fault | None = None
) -> None:
    """Start the batch, let the watchdog cut it off, then let recovery run.

    ``outage`` refuses every terminal write until the watchdog has fired.
    """
    if outage is not None:
        harness.peer.fail_terminals(outage)
    harness.store_members()
    await harness.runtime.rooms.admit(ROOM_ID)
    await harness.adapter.model_entered.wait()
    await harness.advance(CYCLE_SECONDS + 1)
    if outage is not None:
        harness.heal()
    await harness.recover(Entrance.RECONCILIATION)
    await harness.advance(RESYNC_SECONDS)
