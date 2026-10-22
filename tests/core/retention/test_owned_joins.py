"""Cleanup follows active waits rather than task creation ancestry."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from tests.adapters.hold import Hold
from tests.adapters.waiting import until
from tests.core.retention.kit import SETTLE_GRACE_S, ResidenceHost

pytestmark = pytest.mark.timeout(10)


class TestOwnedJoins:
    async def test_a_join_started_after_cleanup_is_not_exempt(
        self, graceful_host: ResidenceHost
    ) -> None:
        host = graceful_host
        resident = host.room()
        await resident.turn()
        request, finish, start_join = Hold(), Hold(), Hold()

        async def child_work() -> None:
            await request.wait()
            with pytest.raises(CleanupIncompleteError):
                await resident.room.dispose()
            await finish.wait()

        child = resident.room.start_owned(child_work)

        async def late_work() -> None:
            await start_join.wait()
            await resident.room.wait_owned(child)

        late = resident.room.start_owned(late_work)
        try:
            await start_join.arrived()
            request.released.set()
            await finish.arrived()
            start_join.released.set()
            await until(lambda: late in resident.room._waiting, failure="join missing")
            await until(lambda: bool(host.clock.timers), failure="grace not armed")
            await host.clock.advance(SETTLE_GRACE_S)
            with pytest.raises(asyncio.CancelledError):
                await late
            assert not child.cancelling()
        finally:
            request.released.set()
            start_join.released.set()
            finish.released.set()
            await asyncio.gather(child, late, return_exceptions=True)
        await resident.room.dispose()

    async def test_detached_creator_has_no_cleanup_exemption(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        request, finish, unrelated = Hold(), Hold(), Hold()
        created: asyncio.Queue[asyncio.Task[None]] = asyncio.Queue()

        async def child_work() -> None:
            await request.wait()
            with pytest.raises(CleanupIncompleteError):
                await resident.room.dispose()
            await finish.wait()

        async def creator_work() -> None:
            created.put_nowait(resident.room.start_owned(child_work))
            await unrelated.wait()

        creator = resident.room.start_owned(creator_work)
        child = await created.get()
        try:
            await unrelated.arrived()
            request.released.set()
            await finish.arrived()
            with pytest.raises(asyncio.CancelledError):
                await creator
            assert not child.cancelling()
            assert resident.live
        finally:
            request.released.set()
            finish.released.set()
            unrelated.released.set()
            await asyncio.gather(creator, child, return_exceptions=True)
        await resident.room.dispose()

    async def test_successor_inherits_active_wait_not_cancelled_creator(
        self, host: ResidenceHost
    ) -> None:
        resident = host.room()
        await resident.turn()
        request, finish = Hold(), Hold()

        async def child_work() -> None:
            await request.wait()
            with pytest.raises(CleanupIncompleteError):
                await resident.room.dispose()
            await finish.wait()

        child = resident.room.start_owned(child_work)

        async def join() -> None:
            await resident.room.wait_owned(child)

        first = resident.room.start_owned(join)
        successor = None
        try:
            await request.arrived()
            await until(lambda: first in resident.room._waiting, failure="join missing")
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            successor = resident.room.start_owned(join)
            await until(
                lambda: successor in resident.room._waiting, failure="successor missing"
            )
            request.released.set()
            await finish.arrived()
            assert first.cancelled()
            assert not successor.cancelling()
            assert not child.cancelling()
        finally:
            request.released.set()
            finish.released.set()
            await asyncio.gather(first, child, return_exceptions=True)
            if successor is not None:
                await successor
        await resident.room.dispose()

    @pytest.mark.parametrize("cleanup", ["dispose", "stop"])
    async def test_dependents_keep_exemption_after_child_finishes(
        self, graceful_host: ResidenceHost, cleanup: str
    ) -> None:
        host = graceful_host
        resident = host.room()
        await resident.turn()
        request, continued, sibling_wait = Hold(), Hold(), Hold()

        async def child_work() -> None:
            await request.wait()
            with pytest.raises(CleanupIncompleteError):
                if cleanup == "dispose":
                    await resident.room.dispose()
                else:
                    await host.owner.stop()

        child = resident.room.start_owned(child_work)

        async def parent_work() -> None:
            await resident.room.wait_owned(child)
            child.result()
            await continued.wait()

        parent = resident.room.start_owned(parent_work)
        sibling = resident.room.start_owned(sibling_wait.wait)
        try:
            await request.arrived()
            await sibling_wait.arrived()
            await until(
                lambda: parent in resident.room._waiting, failure="join missing"
            )
            request.released.set()
            await continued.arrived()
            await host.clock.advance(SETTLE_GRACE_S)
            await until(sibling.done, failure="unrelated work was not cancelled")
            assert sibling.cancelled()
            assert not parent.cancelling()
            assert resident.live and not resident.releases
        finally:
            request.released.set()
            continued.released.set()
            sibling_wait.released.set()
            await asyncio.gather(child, parent, sibling, return_exceptions=True)
        await resident.room.dispose()

    async def test_cancelled_waiter_does_not_cancel_shared_target(
        self, host: ResidenceHost
    ) -> None:
        room = host.room().room
        gate = Hold()
        child = room.start_owned(gate.wait)

        async def join() -> None:
            await room.wait_owned(child)

        first = room.start_owned(join)
        try:
            await gate.arrived()
            await until(lambda: first in room._waiting, failure="join missing")
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not child.cancelling()
            assert first not in room._waiting
            successor = room.start_owned(join)
            gate.released.set()
            await successor
            await child
        finally:
            gate.released.set()
            await asyncio.gather(first, child, return_exceptions=True)

    @pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
    @pytest.mark.parametrize("completed", [False, True], ids=["pending", "completed"])
    async def test_target_is_completion_only(
        self, host: ResidenceHost, outcome: str, completed: bool
    ) -> None:
        room = host.room().room
        gate = Hold()
        failure = RuntimeError("target failed")

        async def work() -> int:
            await gate.wait()
            if outcome == "failure":
                raise failure
            if outcome == "cancelled":
                raise asyncio.CancelledError
            return 42

        task = room.start_owned(work)

        async def join() -> None:
            await room.wait_owned(task)

        waiter = None
        try:
            await gate.arrived()
            if completed:
                gate.released.set()
                await asyncio.wait({task})
            waiter = room.start_owned(join)
            if not completed:
                await until(lambda: waiter in room._waiting, failure="join missing")
                gate.released.set()
            assert await waiter is None
            assert waiter not in room._waiting
            if outcome == "success":
                assert task.result() == 42
            elif outcome == "failure":
                with pytest.raises(RuntimeError) as error:
                    task.result()
                assert error.value is failure
            else:
                with pytest.raises(asyncio.CancelledError):
                    task.result()
        finally:
            gate.released.set()
            await asyncio.gather(task, return_exceptions=True)
            if waiter is not None:
                await asyncio.gather(waiter, return_exceptions=True)

    async def test_pending_foreign_and_self_waits_are_rejected(
        self, host: ResidenceHost
    ) -> None:
        room = host.room().room
        gate = Hold()
        foreign = host.room("other").room.start_owned(gate.wait)

        async def work() -> None:
            with pytest.raises(ValueError, match="accepted"):
                await room.wait_owned(foreign)
            current = asyncio.current_task()
            assert current is not None
            with pytest.raises(ValueError, match="itself"):
                await room.wait_owned(current)

        try:
            await room.start_owned(work)
        finally:
            gate.released.set()
            await foreign
