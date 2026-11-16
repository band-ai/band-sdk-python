"""BackendTurns: each room's backend turn is stopped once, without blocking."""

from __future__ import annotations

import asyncio
import logging

import pytest

from band.core.exceptions import ProviderUnavailableError
from band.core.turn_lifecycle import BACKEND_CANCEL_TIMEOUT_SECONDS, BackendTurns
from band.testing import FakeAgentTools
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
        try:
            async with turns.running(ROOM, HANDLE):
                turns.cancel(ROOM)
                await cancel.entered.wait()
                turns.cancel(ROOM)
                assert cancel.sent == [HANDLE]
                assert cancel.finished == []
        finally:
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
        assert not turns.unavailable(ROOM)
        assert turns.blocker(ROOM) is not None
        assert [(r.levelno, r.exc_info and r.exc_info[0]) for r in caplog.records] == [
            (logging.WARNING, TimeoutError)
        ]

    @pytest.mark.looptime
    async def test_failed_cancelled_start_confirms_without_unavailable(
        self, turns: BackendTurns[str], cancel: RecordingCancel
    ) -> None:
        refused = asyncio.get_running_loop().create_future()
        await self.cancel_while_starting(turns, refused)
        refused.set_exception(RuntimeError("setup failed"))
        await turns.drain()

        assert cancel.sent == []
        assert turns.unconfirmed_handle(ROOM) is None
        assert not turns.unavailable(ROOM)
        await turns.require_admission(ROOM, FakeAgentTools())

    @pytest.mark.looptime
    async def test_stop_deadline_keeps_unconfirmed_until_matching_confirm(
        self, cancel: RecordingCancel
    ) -> None:
        cancel.hold()
        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)
        async with turns.running(ROOM, HANDLE):
            turns.cancel(ROOM)
        await turns.settled(ROOM)
        assert not turns.unavailable(ROOM)
        assert turns.blocker(ROOM) is not None
        turns.confirm_stopped(ROOM, HANDLE)
        cancel.release.set()
        await turns.require_admission(ROOM, FakeAgentTools())

    @pytest.mark.looptime
    async def test_cancel_cancelled_without_evidence_can_latch_unavailable(
        self,
    ) -> None:
        """Cancel callables that own the only evidence latch on CancelledError."""
        entered = asyncio.Event()

        async def cancel_task(handle: str) -> None:
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                turns.mark_unavailable(ROOM)
                raise

        turns = BackendTurns(cancel_task, provider="test", requires_confirmation=True)
        async with turns.running(ROOM, HANDLE):
            turns.cancel(ROOM)
            await entered.wait()
        await turns.settled(ROOM)
        assert turns.unavailable(ROOM)
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission(ROOM, FakeAgentTools())

    @pytest.mark.looptime
    async def test_start_within_cancel_enrolls_late_cleanup(
        self, cancel: RecordingCancel
    ) -> None:
        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)
        release = asyncio.Event()
        prepared = asyncio.Event()

        async def prepare() -> str:
            prepared.set()
            await release.wait()
            return HANDLE

        async def turn() -> None:
            async with turns.running(ROOM):
                await turns.start_within(
                    ROOM, prepare(), timeout=30.0, label=f"setup for {ROOM}"
                )

        task = asyncio.create_task(turn())
        await prepared.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert turns.stopping(ROOM)
        release.set()
        await turns.drain()
        assert cancel.sent == [HANDLE]
        turns.confirm_stopped(ROOM, HANDLE)
        await turns.require_admission(ROOM, FakeAgentTools())

    @pytest.mark.looptime
    async def test_start_within_timeout_enrolls_late_cleanup(
        self, cancel: RecordingCancel
    ) -> None:
        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)
        release = asyncio.Event()
        prepared = asyncio.Event()

        async def prepare() -> str:
            prepared.set()
            await release.wait()
            return HANDLE

        async def turn() -> None:
            async with turns.running(ROOM):
                await turns.start_within(
                    ROOM, prepare(), timeout=0.05, label=f"setup for {ROOM}"
                )

        task = asyncio.create_task(turn())
        await prepared.wait()
        with pytest.raises(TimeoutError):
            await task
        assert not turns.unavailable(ROOM)
        release.set()
        await turns.drain()
        assert cancel.sent == [HANDLE]
        turns.confirm_stopped(ROOM, HANDLE)
        await turns.require_admission(ROOM, FakeAgentTools())

    @pytest.mark.looptime
    async def test_start_within_provider_timeout_does_not_abandon_the_wait(
        self, cancel: RecordingCancel
    ) -> None:
        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)

        async def prepare() -> str:
            raise TimeoutError("provider refused the start")

        with pytest.raises(TimeoutError, match="provider refused the start"):
            async with turns.running(ROOM):
                await turns.start_within(
                    ROOM, prepare(), timeout=30.0, label=f"setup for {ROOM}"
                )
        assert not turns.stopping(ROOM)
        assert turns.blocker(ROOM) is None
        assert cancel.sent == []
        await turns.require_admission(ROOM, FakeAgentTools())

    @pytest.mark.looptime
    async def test_failed_start_keeps_abandon_start_unavailable(
        self, cancel: RecordingCancel
    ) -> None:
        turns = BackendTurns(cancel, provider="test", requires_confirmation=True)
        started_prep = asyncio.Event()

        async def prepare() -> str:
            started_prep.set()
            await asyncio.sleep(0)
            turns.abandon_start(ROOM)
            raise RuntimeError("create failed after possible acceptance")

        prepare_fut = asyncio.ensure_future(prepare())
        await started_prep.wait()
        turns.abandon_start_wait(ROOM, prepare_fut)
        await turns.drain()
        assert turns.unavailable(ROOM)
        with pytest.raises(ProviderUnavailableError):
            await turns.require_admission(ROOM, FakeAgentTools())

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
