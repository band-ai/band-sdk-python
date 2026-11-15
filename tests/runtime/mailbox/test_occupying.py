"""occupying is true only on the room's worker task."""

from __future__ import annotations

import asyncio

from band.runtime.mailbox import RoomMailbox
from tests.conftest import eager_tasks, requires_eager_tasks
from tests.runtime.mailbox.kit import JobHold


class TestOccupying:
    @requires_eager_tasks
    async def test_running_job_identifies_itself_with_eager_tasks(self) -> None:
        mailbox = RoomMailbox()

        async def job() -> bool:
            return mailbox.occupying("room-1")

        try:
            with eager_tasks():
                assert await mailbox.ask("room-1", job) is True
        finally:
            await mailbox.close("room-1")

    async def test_spawned_child_is_not_the_running_job(self) -> None:
        mailbox = RoomMailbox()

        async def child() -> bool:
            return mailbox.occupying("room-1")

        async def job() -> bool:
            return await asyncio.create_task(child())

        try:
            assert await mailbox.ask("room-1", job) is False
        finally:
            await mailbox.close("room-1")

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
