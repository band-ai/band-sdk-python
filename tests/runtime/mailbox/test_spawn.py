"""The spawn future settles with its enqueued job."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.mailbox import RoomMailbox


class TestSpawn:
    async def test_future_completes_after_the_job_finishes(self) -> None:
        mailbox = RoomMailbox()
        entered = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def job() -> None:
            entered.set()
            await release.wait()
            finished.set()

        future = mailbox.spawn("room-1", job)
        await entered.wait()
        assert not future.done()

        release.set()
        await future
        assert finished.is_set()
        await mailbox.close("room-1")

    async def test_future_propagates_the_job_error(self) -> None:
        mailbox = RoomMailbox()
        error = RuntimeError("spawn failed")

        async def job() -> None:
            raise error

        future = mailbox.spawn("room-1", job)
        with pytest.raises(RuntimeError) as raised:
            await future

        assert raised.value is error
        await mailbox.close("room-1")
