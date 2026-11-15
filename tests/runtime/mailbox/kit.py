"""Holds that pause a mailbox job until the test releases it."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from band.runtime.mailbox import RoomMailbox

MailboxSubmit = Callable[
    [RoomMailbox, str, Callable[[], Awaitable[None]]], Awaitable[None]
]


class JobHold:
    """One pause point: the job sets ``entered``, then waits for ``release``."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    async def pause(self) -> None:
        self.entered.set()
        await self.release.wait()


class SettlingJob:
    """Delay cancellation cleanup and optionally suppress or replace its error."""

    def __init__(self, error: BaseException | None) -> None:
        self.running = JobHold()
        self.running.hold()
        self.settling = JobHold()
        self.settling.hold()
        self.finished = asyncio.Event()
        self.error = error

    async def run(self) -> str:
        try:
            await self.running.pause()
        except asyncio.CancelledError:
            try:
                await self.settling.pause()
                if self.error is not None:
                    raise self.error
                return "cancellation suppressed"
            finally:
                self.finished.set()
        return "completed"


async def checkpoint() -> None:
    """Let already scheduled task wakeups run before resuming the test."""
    reached = asyncio.Event()
    asyncio.get_running_loop().call_soon(reached.set)
    await reached.wait()


async def cancel_at_handoff(
    mailbox: RoomMailbox, room_id: str, submit: MailboxSubmit
) -> None:
    """Cancel between releasing a predecessor and entering the next handler."""
    hold = JobHold()
    hold.hold()
    loop = asyncio.get_running_loop()
    effects: list[str] = []

    async def predecessor() -> None:
        await hold.pause()
        # Revoke after the worker resumes but before the handler task starts.
        loop.call_soon(loop.call_soon, revoke)

    def revoke() -> None:
        if pending is not None:
            effects.append("revoked")
            pending.cancel()

    async def cancelled_handler() -> None:
        effects.append("cancelled handler")

    async def successor(label: str) -> None:
        effects.append(label)

    blocker = mailbox.spawn(room_id, predecessor)
    pending: asyncio.Future[None] | None = None
    try:
        await hold.entered.wait()
        pending = asyncio.ensure_future(submit(mailbox, room_id, cancelled_handler))
        loop.call_soon(hold.release.set)
        try:
            await pending
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("handoff cancellation must reach the waiter")
        first = mailbox.spawn(room_id, lambda: successor("first successor"))
        second = mailbox.spawn(room_id, lambda: successor("second successor"))
        await asyncio.gather(first, second)
        assert effects == ["revoked", "first successor", "second successor"], effects
    finally:
        hold.release.set()
        tasks = [blocker, *([pending] if pending is not None else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await mailbox.close(room_id)
