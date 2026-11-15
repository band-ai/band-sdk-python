"""close drains queued jobs, then a later ask starts a new worker."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.mailbox import MailboxClosing, NestedMailboxAsk, RoomMailbox
from tests.runtime.mailbox.kit import JobHold


class TestClose:
    async def test_concurrent_close_callers_share_the_drain_and_reopen(self) -> None:
        mailbox = RoomMailbox()
        room_id = "room-1"
        hold = JobHold()
        hold.hold()
        active_finished = asyncio.Event()

        async def occupied() -> None:
            await hold.pause()
            active_finished.set()

        waiting = asyncio.create_task(mailbox.ask(room_id, occupied))
        close_tasks: list[asyncio.Task[None]] = []
        try:
            await hold.entered.wait()

            first_started = asyncio.Event()
            second_started = asyncio.Event()

            async def close_room(started: asyncio.Event) -> None:
                started.set()
                await mailbox.close(room_id)

            first_close = asyncio.create_task(close_room(first_started))
            second_close = asyncio.create_task(close_room(second_started))
            close_tasks.extend((first_close, second_close))
            await first_started.wait()
            await second_started.wait()

            assert not active_finished.is_set()
            assert not first_close.done()
            assert not second_close.done()

            hold.release.set()
            await waiting
            await asyncio.gather(first_close, second_close)
            assert active_finished.is_set()

            async def later() -> str:
                return "reopened"

            assert await mailbox.ask(room_id, later) == "reopened"
        finally:
            hold.release.set()
            await asyncio.gather(waiting, *close_tasks, return_exceptions=True)
            await mailbox.close(room_id)

    async def test_close_rejects_submissions_while_draining(self) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()

        async def occupied() -> None:
            await hold.pause()

        waiting = asyncio.create_task(mailbox.ask("room-1", occupied))
        await hold.entered.wait()

        closing_started = asyncio.Event()

        async def close_room() -> None:
            closing_started.set()
            await mailbox.close("room-1")

        closing = asyncio.create_task(close_room())
        await closing_started.wait()

        async def rejected() -> None:
            raise AssertionError("a rejected job must not run")

        with pytest.raises(MailboxClosing):
            mailbox.cast("room-1", rejected)

        hold.release.set()
        await waiting
        await closing

        async def later() -> str:
            return "reopened"

        assert await mailbox.ask("room-1", later) == "reopened"
        await mailbox.close("room-1")

    async def test_close_runs_queued_work_then_a_later_ask_uses_a_new_worker(
        self,
    ) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()
        queued_entered = asyncio.Event()
        release_queued = asyncio.Event()
        drained = asyncio.Event()

        async def occupied() -> None:
            await hold.pause()

        async def queued() -> None:
            queued_entered.set()
            await release_queued.wait()
            drained.set()

        waiting = asyncio.create_task(mailbox.ask("room-1", occupied))
        close_tasks: list[asyncio.Task[None]] = []
        try:
            await hold.entered.wait()
            mailbox.cast("room-1", queued)
            closing = asyncio.create_task(mailbox.close("room-1"))
            close_tasks.append(closing)
            hold.release.set()
            await waiting
            await queued_entered.wait()

            assert not drained.is_set()
            assert not closing.done()

            release_queued.set()
            await drained.wait()
            await closing
        finally:
            hold.release.set()
            release_queued.set()
            await asyncio.gather(waiting, *close_tasks, return_exceptions=True)
            await mailbox.close("room-1")

        async def later() -> str:
            return "ok"

        assert await mailbox.ask("room-1", later) == "ok"
        await mailbox.close("room-1")

    async def test_close_from_the_worker_raises(self) -> None:
        mailbox = RoomMailbox()

        async def closing() -> None:
            await mailbox.close("room-1")

        with pytest.raises(NestedMailboxAsk):
            await mailbox.ask("room-1", closing)
        await mailbox.close("room-1")

    async def test_close_from_one_room_job_cannot_close_another_room(self) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()

        async def occupied() -> None:
            await hold.pause()

        waiting = asyncio.create_task(mailbox.ask("room-b", occupied))
        close_attempted = asyncio.Event()

        async def close_other_room() -> None:
            close_attempted.set()
            await mailbox.close("room-b")

        closing = asyncio.create_task(mailbox.ask("room-a", close_other_room))
        try:
            await hold.entered.wait()
            await close_attempted.wait()
            hold.release.set()

            with pytest.raises(NestedMailboxAsk):
                await closing
        finally:
            hold.release.set()
            await asyncio.gather(waiting, closing, return_exceptions=True)
            await asyncio.gather(mailbox.close("room-a"), mailbox.close("room-b"))

    async def test_cancelling_close_does_not_cancel_the_running_job(self) -> None:
        mailbox = RoomMailbox()
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def occupied() -> str:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            finished.set()
            return "finished"

        waiting = asyncio.create_task(mailbox.ask("room-1", occupied))
        await entered.wait()
        closing_started = asyncio.Event()

        async def close_room() -> None:
            closing_started.set()
            await mailbox.close("room-1")

        closing = asyncio.create_task(close_room())
        await closing_started.wait()

        async def rejected() -> None:
            raise AssertionError("a rejected job must not run")

        with pytest.raises(MailboxClosing):
            mailbox.cast("room-1", rejected)

        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert not cancelled.is_set()

        release.set()
        assert await waiting == "finished"
        assert finished.is_set()
        await mailbox.close("room-1")
