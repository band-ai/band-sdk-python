"""Mailbox jobs do not inherit the submitter's context variables."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

from band.runtime.mailbox import RoomMailbox
from tests.runtime.mailbox.kit import JobHold

REQUEST_CONTEXT = ContextVar("mailbox_request_context", default=None)
QUEUED_RESULT = "done"


class TestContextIsolation:
    async def test_queued_job_does_not_inherit_the_first_submitters_context(
        self,
    ) -> None:
        mailbox = RoomMailbox()
        hold = JobHold()
        hold.hold()
        seen_contexts: list[str | None] = []

        async def occupied() -> None:
            seen_contexts.append(REQUEST_CONTEXT.get())
            await hold.pause()

        first_token = REQUEST_CONTEXT.set("first-turn")
        first = asyncio.create_task(mailbox.ask("room-1", occupied))
        REQUEST_CONTEXT.reset(first_token)
        await hold.entered.wait()

        submitted = asyncio.Event()

        async def queued() -> str:
            seen_contexts.append(REQUEST_CONTEXT.get())
            return QUEUED_RESULT

        async def submit_queued() -> str:
            submitted.set()
            # ask enqueues synchronously before it suspends on its result future.
            return await mailbox.ask("room-1", queued)

        second_token = REQUEST_CONTEXT.set("second-turn")
        second = asyncio.create_task(submit_queued())
        REQUEST_CONTEXT.reset(second_token)
        await submitted.wait()

        hold.release.set()
        await first
        assert await second == QUEUED_RESULT
        assert seen_contexts == [None, None]
        await mailbox.close("room-1")
