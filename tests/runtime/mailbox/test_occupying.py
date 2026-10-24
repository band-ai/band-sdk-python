"""occupying is true only on the room's worker task."""

from __future__ import annotations

import asyncio

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import JobHold


class TestOccupying:
    async def test_occupying_is_true_on_the_worker_and_false_on_the_waiter(
        self,
    ) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()
        seen: list[bool] = []

        async def job() -> None:
            seen.append(mailbox.occupying("room-1"))
            await hold.pause()

        waiting = asyncio.create_task(mailbox.ask("room-1", job))
        await hold.entered.wait()
        assert mailbox.occupying("room-1") is False
        hold.release.set()
        await waiting
        assert seen == [True]
        await mailbox.close("room-1")
