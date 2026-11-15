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

    async def test_room_job_can_cast_follow_up_after_it_finishes(self) -> None:
        mailbox = RoomMailbox()
        room_id = "room-1"
        parent_entered = asyncio.Event()
        release_parent = asyncio.Event()
        follow_up_finished = asyncio.Event()
        order: list[str] = []

        async def parent() -> None:
            parent_entered.set()
            mailbox.cast(room_id, follow_up)
            await release_parent.wait()
            order.append("parent-finished")

        async def follow_up() -> None:
            order.append("follow-up")
            follow_up_finished.set()

        waiting = asyncio.create_task(mailbox.ask(room_id, parent))
        try:
            await parent_entered.wait()
            assert order == []
            release_parent.set()

            await waiting
            await follow_up_finished.wait()

            assert order == ["parent-finished", "follow-up"]
        finally:
            release_parent.set()
            await asyncio.gather(waiting, return_exceptions=True)
            await mailbox.close(room_id)
