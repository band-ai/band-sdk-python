"""close drains queued jobs, then a later ask starts a new worker."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.mailbox import MailboxClosing, NestedMailboxAsk, RoomMailbox
from tests.runtime.mailbox.kit import JobHold


class TestClose:
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
        drained = asyncio.Event()

        async def occupied() -> None:
            await hold.pause()

        async def queued() -> None:
            drained.set()

        waiting = asyncio.create_task(mailbox.ask("room-1", occupied))
        await hold.entered.wait()
        mailbox.cast("room-1", queued)
        closing = asyncio.create_task(mailbox.close("room-1"))
        hold.release.set()
        await waiting
        await drained.wait()
        await closing

        async def later() -> str:
            return "ok"

        assert await mailbox.ask("room-1", later) == "ok"
        await mailbox.close("room-1")

    async def test_close_from_the_worker_raises(self) -> None:
        mailbox = RoomMailbox()

        async def closing() -> None:
            await mailbox.close("room-1")

        with pytest.raises(NestedMailboxAsk, match="cannot close"):
            await mailbox.ask("room-1", closing)
        await mailbox.close("room-1")
