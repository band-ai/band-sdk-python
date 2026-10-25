"""The spawn future settles with its enqueued job."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.mailbox import NestedMailboxAsk, RoomMailbox


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

    async def test_future_cancellation_cancels_the_job_and_releases_the_worker(
        self,
    ) -> None:
        mailbox = RoomMailbox()
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()

        async def job() -> None:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        future = mailbox.spawn("room-1", job)
        await entered.wait()
        future.cancel()

        await cancelled.wait()
        assert future.cancelled()

        async def next_job() -> str:
            return "next"

        assert await mailbox.ask("room-1", next_job) == "next"
        await mailbox.close("room-1")

    async def test_cancelled_queued_future_skips_its_job_before_the_next_job(
        self,
    ) -> None:
        mailbox = RoomMailbox()
        room_id = "room-1"
        entered = asyncio.Event()
        release = asyncio.Event()
        canceled_body_ran = asyncio.Event()
        following_body_ran = asyncio.Event()

        async def held_job() -> None:
            entered.set()
            await release.wait()

        async def canceled_job() -> None:
            canceled_body_ran.set()

        async def following_job() -> None:
            following_body_ran.set()

        active_future = mailbox.spawn(room_id, held_job)
        following_future: asyncio.Future[None] | None = None
        try:
            await entered.wait()
            canceled_future = mailbox.spawn(room_id, canceled_job)
            assert not canceled_body_ran.is_set()
            canceled_future.cancel()
            following_future = mailbox.spawn(room_id, following_job)

            release.set()
            await active_future
            await following_future

            assert canceled_future.cancelled()
            assert not canceled_body_ran.is_set()
            assert following_body_ran.is_set()
        finally:
            release.set()
            await asyncio.gather(active_future, return_exceptions=True)
            if following_future is not None:
                await asyncio.gather(following_future, return_exceptions=True)
            await mailbox.close(room_id)

    async def test_spawn_from_its_room_job_raises(self) -> None:
        mailbox = RoomMailbox()

        async def nested() -> None:
            await mailbox.spawn("room-1", lambda: asyncio.sleep(0))

        with pytest.raises(NestedMailboxAsk, match="would wait forever"):
            await mailbox.ask("room-1", nested)
        await mailbox.close("room-1")
