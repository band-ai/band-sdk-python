"""The worker must not ask itself."""

from __future__ import annotations

import pytest

from band.runtime.mailbox import NestedMailboxAsk, RoomMailbox


class TestNestedAsk:
    async def test_ask_from_the_room_worker_raises(self) -> None:
        mailbox = RoomMailbox()

        async def nested() -> None:
            await mailbox.ask("room-1", inner)

        async def inner() -> None:
            return None

        with pytest.raises(NestedMailboxAsk, match="would wait forever"):
            await mailbox.ask("room-1", nested)
        await mailbox.close("room-1")

    async def test_ask_from_one_room_worker_may_ask_another_room(self) -> None:
        mailbox = RoomMailbox()

        async def from_a() -> str:
            return await mailbox.ask("room-b", from_b)

        async def from_b() -> str:
            return "ok"

        assert await mailbox.ask("room-a", from_a) == "ok"
        await mailbox.close("room-a")
        await mailbox.close("room-b")
