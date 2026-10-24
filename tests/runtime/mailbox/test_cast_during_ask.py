"""A cast runs on the worker after the in-flight ask, not by copying context."""

from __future__ import annotations

import asyncio

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import JobHold


class TestCastDuringAsk:
    async def test_cast_waits_for_the_held_ask_then_runs_on_the_worker(self) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()
        order: list[str] = []
        occupied_here: list[bool] = []

        async def occupied() -> None:
            occupied_here.append(mailbox.occupying("room-1"))
            order.append("ask")
            await hold.pause()

        async def follow_up() -> None:
            occupied_here.append(mailbox.occupying("room-1"))
            order.append("cast")
            finished.set()

        finished = asyncio.Event()
        waiting = asyncio.create_task(mailbox.ask("room-1", occupied))
        await hold.entered.wait()
        mailbox.cast("room-1", follow_up)
        assert order == ["ask"]
        hold.release.set()
        await waiting
        await finished.wait()
        assert order == ["ask", "cast"]
        assert occupied_here == [True, True]
        await mailbox.close("room-1")
