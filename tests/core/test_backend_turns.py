"""BackendTurns: each room's backend turn is stopped once, without blocking."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.core.turn_lifecycle import BACKEND_CANCEL_TIMEOUT_SECONDS, BackendTurns
from tests.core.recordingcancel import RecordingCancel

ROOM = "room-1"
OTHER_ROOM = "room-2"
HANDLE = "turn-1"
LIFECYCLE_LOGGER = "band.core.turn_lifecycle"


class TestBackendTurns:
    @pytest.fixture
    def cancel(self) -> RecordingCancel:
        return RecordingCancel()

    @pytest.fixture
    def turns(self, cancel: RecordingCancel) -> BackendTurns[str]:
        return BackendTurns(cancel, provider="test")

    async def test_cancelled_turn_stops_its_backend_once(
        self, turns: BackendTurns[str], cancel: RecordingCancel
    ) -> None:
        async def turn() -> None:
            async with turns.running(ROOM, HANDLE):
                await asyncio.Future[None]()

        task = asyncio.create_task(turn())
        await asyncio.sleep(0)
        turns.cancel(ROOM)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await turns.settled(ROOM)

        assert cancel.sent == [HANDLE]

    async def test_idle_room_sends_nothing(
        self, turns: BackendTurns[str], cancel: RecordingCancel
    ) -> None:
        turns.cancel(ROOM)
        await turns.drain()

        assert cancel.sent == []

    async def test_stop_before_the_handle_is_known_is_sent_once_it_binds(
        self, turns: BackendTurns[str], cancel: RecordingCancel
    ) -> None:
        async with turns.running(ROOM):
            turns.cancel(ROOM)
            await turns.drain()
            assert cancel.sent == []
            turns.bind(ROOM, HANDLE)
            await turns.settled(ROOM)

        assert cancel.sent == [HANDLE]

    @staticmethod
    async def cancel_while_starting(
        turns: BackendTurns[str], start: asyncio.Future[str]
    ) -> None:
        """Cancel a turn whose owner is still awaiting ``start``."""

        async def turn() -> None:
            async with turns.running(ROOM):
                await turns.started(ROOM, start)

        task = asyncio.create_task(turn())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_a_turn_cancelled_while_it_starts_is_stopped_once_it_starts(
        self, turns: BackendTurns[str], cancel: RecordingCancel
    ) -> None:
        accepted = asyncio.get_running_loop().create_future()
        await self.cancel_while_starting(turns, accepted)
        assert cancel.sent == []
        accepted.set_result(HANDLE)
        await turns.drain()

        assert cancel.sent == [HANDLE]

    async def test_a_start_that_fails_after_its_turn_was_cancelled_stops_nothing(
        self,
        turns: BackendTurns[str],
        cancel: RecordingCancel,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        refused = asyncio.get_running_loop().create_future()
        await self.cancel_while_starting(turns, refused)
        with caplog.at_level(logging.WARNING):
            refused.set_exception(RuntimeError("turn refused"))
            await turns.drain()

        assert cancel.sent == []
        assert caplog.records == []

    async def test_a_stop_that_never_reached_the_backend_is_logged(
        self,
        turns: BackendTurns[str],
        cancel: RecordingCancel,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.INFO, logger=LIFECYCLE_LOGGER):
            async with turns.running(ROOM):
                turns.cancel(ROOM)

        assert cancel.sent == []
        assert [r.getMessage() for r in caplog.records] == [
            (
                f"test turn in room {ROOM} ended before its backend revealed it; "
                "no backend stop was sent"
            )
        ]

    async def test_cancel_returns_while_the_backend_call_is_in_flight(
        self, turns: BackendTurns[str], cancel: RecordingCancel
    ) -> None:
        cancel.hold()
        async with turns.running(ROOM, HANDLE):
            turns.cancel(ROOM)
            await asyncio.sleep(0)
            assert cancel.sent == [HANDLE]
            assert cancel.finished == []
        cancel.release.set()
        await turns.drain()

        assert cancel.finished == [HANDLE]

    @pytest.mark.parametrize("join", ["next_turn", "settled", "drain"])
    async def test_waiters_join_a_pending_stop(
        self, turns: BackendTurns[str], cancel: RecordingCancel, join: str
    ) -> None:
        cancel.hold()
        async with turns.running(ROOM, HANDLE):
            turns.cancel(ROOM)

        async def wait() -> None:
            match join:
                case "next_turn":
                    async with turns.running(ROOM, "turn-2"):
                        pass
                case "settled":
                    await turns.settled(ROOM)
                case "drain":
                    await turns.drain()

        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        assert not waiter.done()
        cancel.release.set()
        await waiter

        assert cancel.finished == [HANDLE]

    async def test_a_stop_reaches_only_its_room(
        self, turns: BackendTurns[str], cancel: RecordingCancel
    ) -> None:
        async with turns.running(ROOM, HANDLE), turns.running(OTHER_ROOM, "turn-2"):
            turns.cancel(ROOM)
            await turns.drain()

        assert cancel.sent == [HANDLE]

    @pytest.mark.looptime
    async def test_a_hanging_stop_is_abandoned_and_logged_once(
        self,
        turns: BackendTurns[str],
        cancel: RecordingCancel,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cancel.hold()
        loop = asyncio.get_running_loop()
        async with turns.running(ROOM, HANDLE):
            turns.cancel(ROOM)
        started = loop.time()
        with caplog.at_level(logging.WARNING):
            await turns.settled(ROOM)

        assert loop.time() - started == BACKEND_CANCEL_TIMEOUT_SECONDS
        assert cancel.finished == []
        assert [(r.levelno, r.exc_info and r.exc_info[0]) for r in caplog.records] == [
            (logging.WARNING, TimeoutError)
        ]

    @pytest.mark.parametrize("ending", [TimeoutError, RuntimeError, None])
    async def test_a_turn_that_ends_itself_sends_nothing(
        self,
        turns: BackendTurns[str],
        cancel: RecordingCancel,
        ending: type[Exception] | None,
    ) -> None:
        async def turn() -> None:
            async with turns.running(ROOM, HANDLE):
                if ending is not None:
                    raise ending

        if ending is None:
            await turn()
        else:
            with pytest.raises(ending):
                await turn()
        turns.cancel(ROOM)
        await turns.drain()

        assert cancel.sent == []
