"""Jobs for one room do not wait on another room's worker."""

from __future__ import annotations

import asyncio

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import JobHold


class TestRoomIsolation:
    async def test_second_room_completes_while_the_first_is_held(self) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()
        finished: list[str] = []

        async def slow() -> str:
            await hold.pause()
            finished.append("a")
            return "a"

        async def fast() -> str:
            finished.append("b")
            return "b"

        waiting = asyncio.create_task(mailbox.ask("room-a", slow))
        await hold.entered.wait()
        assert await mailbox.ask("room-b", fast) == "b"
        assert finished == ["b"]
        assert not waiting.done()
        hold.release.set()
        assert await waiting == "a"
        assert finished == ["b", "a"]
        await mailbox.close("room-a")
        await mailbox.close("room-b")
