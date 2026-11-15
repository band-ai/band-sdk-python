"""Cancelling ask cancels the in-flight job so the worker is not pinned."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import JobHold


class TestAskCancel:
    async def test_cancelling_ask_cancels_the_running_job(self) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()
        cancelled = asyncio.Event()

        async def occupied() -> None:
            try:
                await hold.pause()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        waiting = asyncio.create_task(mailbox.ask("room-1", occupied))
        await hold.entered.wait()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await cancelled.wait()

        async def later() -> str:
            return "ok"

        assert await mailbox.ask("room-1", later) == "ok"
        await mailbox.close("room-1")
