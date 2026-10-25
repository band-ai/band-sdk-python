"""The worker must not ask itself."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.mailbox import NestedMailboxAsk, RoomMailbox
from tests.conftest import eager_tasks, requires_eager_tasks


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

    async def test_ask_from_a_child_task_of_the_room_job_raises(self) -> None:
        mailbox = RoomMailbox()

        async def inner() -> None:
            return None

        async def nested() -> None:
            async def child() -> None:
                await mailbox.ask("room-1", inner)

            await asyncio.create_task(child())

        with pytest.raises(NestedMailboxAsk, match="would wait forever"):
            await mailbox.ask("room-1", nested)
        await mailbox.close("room-1")

    @requires_eager_tasks
    async def test_ask_from_the_room_job_raises_with_an_eager_task_factory(
        self,
    ) -> None:
        mailbox = RoomMailbox()

        async def nested() -> None:
            await mailbox.ask("room-1", inner)

        async def inner() -> None:
            return None

        with eager_tasks(), pytest.raises(NestedMailboxAsk, match="would wait forever"):
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
