"""Room teardown stays owned until stop and cleanup both succeed.

Rooms come and go through the callbacks ``RoomPresence`` invokes on its
runtime, and every outcome is read from what a caller can see: the runtime's
live executions, ``runtime.stop()``'s result, the fake executions' stop counts
and the session-cleanup callback. Retry waits run on looptime's virtual clock.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import CREATION_RETRY_WAIT_S, AgentRuntime
from band.runtime.types import SessionConfig
from tests.runtime.conftest import make_link_mock, platform_msg, wait_for_condition

ROOM = "room-1"
#: Virtual seconds, ample for any number of creation retries.
RETRY_HORIZON_S = 10 * CREATION_RETRY_WAIT_S


async def elapse(seconds: float) -> None:
    """Let ``seconds`` of looptime's virtual clock pass."""
    with suppress(TimeoutError):
        await asyncio.wait_for(asyncio.Event().wait(), timeout=seconds)


class Room:
    """One ExecutionContext driven through /next, with no idle release."""

    def __init__(self, room_id: str = ROOM) -> None:
        self.link = make_link_mock()
        self.pending: list[Any] = []

        async def next_message(*_args: Any, **_kwargs: Any) -> Any:
            return self.pending.pop(0) if self.pending else None

        self.link.get_next_message.side_effect = next_message
        self.link.get_stale_processing_messages = AsyncMock(return_value=[])
        self.turn_started = asyncio.Event()
        self.turn_gate: asyncio.Event | None = None
        self.ctx = ExecutionContext(
            room_id,
            self.link,
            self.on_execute,
            config=SessionConfig(
                idle_resync_seconds=30.0,
                enable_working_state=False,
            ),
        )

    async def on_execute(self, _ctx: Any, _event: Any) -> None:
        self.turn_started.set()
        if self.turn_gate is not None:
            await self.turn_gate.wait()

    async def send(self, msg_id: str) -> None:
        """Deliver one message through /next and wait until its turn starts."""
        self.turn_started.clear()
        self.pending.append(platform_msg(msg_id))
        await self.ctx.request_resync()
        await asyncio.wait_for(self.turn_started.wait(), timeout=5.0)


@pytest_asyncio.fixture(loop_scope="function")
async def room():
    rooms: list[Room] = []

    def make() -> Room:
        rooms.append(Room())
        return rooms[-1]

    yield make
    for r in rooms:
        if r.turn_gate is not None:
            r.turn_gate.set()
        await r.ctx.stop()


async def test_a_stop_cancelled_while_the_loop_unwinds_is_not_swallowed(room) -> None:
    """The room loop swallows its own cancellation; a cancel of stop() landing
    while stop() waits on that loop must still end stop(), not be absorbed."""
    r = room()
    await r.ctx.start()
    stopping = asyncio.create_task(r.ctx.stop())
    await asyncio.wait({stopping}, timeout=0)
    assert (r.ctx.is_running, stopping.done()) == (False, False), (
        "stop() began and is still waiting on the cancelled loop"
    )

    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stopping, timeout=5.0)

    await asyncio.wait_for(r.ctx.stop(), timeout=5.0)
    assert r.ctx.is_running is False


class GatedExecution:
    """An execution whose stop() waits for the test, counting its calls."""

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.stop_calls = 0
        self.stop_entered = asyncio.Event()
        self.stop_gate = asyncio.Event()
        self.stop_errors: list[Exception] = []
        self.start_error: Exception | None = None
        self.start_gate: asyncio.Event | None = None
        self.start_entered = asyncio.Event()
        self.start_cancelled = False

    async def start(self) -> None:
        self.start_entered.set()
        if self.start_gate is not None:
            try:
                await self.start_gate.wait()
            except asyncio.CancelledError:
                self.start_cancelled = True
                raise
        if self.start_error is not None:
            raise self.start_error

    async def stop(self, timeout: float | None = None) -> bool:
        self.stop_calls += 1
        self.stop_entered.set()
        await self.stop_gate.wait()
        if self.stop_errors:
            raise self.stop_errors.pop(0)
        return True

    async def on_event(self, event: Any) -> None: ...


class GenerationRuntime:
    """An AgentRuntime building a new GatedExecution per room join."""

    def __init__(self) -> None:
        self.generations: list[GatedExecution] = []
        self.cleanups: list[tuple[str, int | None]] = []
        self.cleanup_errors: list[Exception] = []
        self.build_errors: list[Exception] = []
        self.start_errors: list[Exception] = []
        self.hold_starts = False

        def build(*_args: Any, **_kwargs: Any) -> GatedExecution:
            if self.build_errors:
                raise self.build_errors.pop(0)
            execution = GatedExecution(len(self.generations))
            if self.hold_starts:
                execution.start_gate = asyncio.Event()
            if self.start_errors:
                execution.start_error = self.start_errors.pop(0)
            self.generations.append(execution)
            return execution

        async def cleanup(room_id: str) -> None:
            live = self.runtime.executions.get(room_id)
            self.cleanups.append((room_id, getattr(live, "generation", None)))
            if self.cleanup_errors:
                raise self.cleanup_errors.pop(0)

        self.runtime = AgentRuntime(
            make_link_mock(),
            "agent-1",
            AsyncMock(),
            execution_factory=build,
            on_session_cleanup=cleanup,
        )

    async def join(self) -> None:
        """The room is admitted: presence tells its runtime."""
        await self.runtime.presence.on_room_joined(ROOM, {})

    async def leave(self) -> None:
        """The room is removed: presence tells its runtime."""
        await self.runtime.presence.on_room_left(ROOM)

    async def joined_and_left(self) -> GatedExecution:
        """Run one execution through a clean join and leave."""
        await self.join()
        [old] = self.generations
        old.stop_gate.set()
        await self.leave()
        return old

    async def until_live(self) -> None:
        await wait_for_condition(
            lambda: ROOM in self.runtime.executions, timeout=RETRY_HORIZON_S
        )


@pytest.fixture
def harness() -> GenerationRuntime:
    return GenerationRuntime()


async def test_overlapping_stops_share_one_stop_and_one_cleanup(harness) -> None:
    await harness.join()
    [first] = harness.generations

    callers = [asyncio.create_task(harness.runtime.stop()) for _ in range(2)]
    await first.stop_entered.wait()
    first.stop_gate.set()
    results = await asyncio.gather(*callers)

    assert (first.stop_calls, harness.cleanups, results) == (
        1,
        [(ROOM, None)],
        [True, True],
    )


@pytest.mark.looptime
async def test_a_rejoin_waits_for_the_previous_rooms_cleanup(harness) -> None:
    await harness.join()
    [old] = harness.generations
    leaving = asyncio.create_task(harness.runtime.stop())
    await old.stop_entered.wait()
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving

    rejoining = asyncio.create_task(harness.join())
    done, _ = await asyncio.wait({rejoining}, timeout=CREATION_RETRY_WAIT_S)
    assert (done, len(harness.generations)) == (set(), 1)
    old.stop_gate.set()
    await asyncio.wait_for(rejoining, timeout=5.0)

    assert harness.cleanups == [(ROOM, None)], "old cleanup never saw the new room"
    assert (old.stop_calls, harness.runtime.executions[ROOM]) == (
        1,
        harness.generations[1],
    )


async def test_a_failed_stop_keeps_the_room_until_a_retry_stops_it(harness) -> None:
    await harness.join()
    [old] = harness.generations
    old.stop_gate.set()
    old.stop_errors.append(RuntimeError("process still running"))

    first = await harness.runtime.stop()

    assert (first, old.stop_calls, harness.cleanups) == (False, 1, [])
    second = await harness.runtime.stop()
    assert (second, old.stop_calls, harness.cleanups) == (True, 2, [(ROOM, None)])


@pytest.mark.looptime
async def test_a_rejoin_past_a_failing_stop_is_created_once_the_stop_succeeds(
    harness,
) -> None:
    await harness.join()
    [old] = harness.generations
    old.stop_gate.set()
    old.stop_errors.extend(RuntimeError("still running") for _ in range(3))
    await harness.leave()

    await harness.join()

    assert (ROOM in harness.runtime.executions, harness.cleanups) == (False, [])
    await harness.until_live()
    assert (old.stop_calls, harness.cleanups) == (4, [(ROOM, None)])
    assert (len(harness.generations), harness.runtime.executions[ROOM]) == (
        2,
        harness.generations[1],
    )


async def test_a_room_removal_preempts_a_graceful_shutdown(room, monkeypatch) -> None:
    r = room()
    r.turn_gate = asyncio.Event()
    cleanups: list[str] = []

    async def cleanup(room_id: str) -> None:
        cleanups.append(room_id)

    runtime = AgentRuntime(
        r.link,
        "agent-1",
        AsyncMock(),
        execution_factory=lambda *_args, **_kwargs: r.ctx,
        on_session_cleanup=cleanup,
    )
    await runtime.presence.on_room_joined(ROOM, {})
    await r.send("msg-1")
    stop_timeouts: list[float | None] = []
    running_stops = 0
    most_concurrent_stops = 0
    real_stop = r.ctx.stop

    async def tracked_stop(timeout: float | None = None) -> bool:
        nonlocal running_stops, most_concurrent_stops
        stop_timeouts.append(timeout)
        running_stops += 1
        most_concurrent_stops = max(most_concurrent_stops, running_stops)
        try:
            return await real_stop(timeout=timeout)
        finally:
            running_stops -= 1

    monkeypatch.setattr(r.ctx, "stop", tracked_stop)
    shutting_down = asyncio.create_task(runtime.stop(timeout=30.0))
    await wait_for_condition(lambda: stop_timeouts == [30.0], timeout=5.0)

    await asyncio.wait_for(runtime.presence.on_room_left(ROOM), timeout=5.0)

    assert not r.turn_gate.is_set()
    assert (cleanups, most_concurrent_stops, stop_timeouts) == ([ROOM], 1, [30.0, None])
    assert await asyncio.wait_for(shutting_down, timeout=5.0) is False


@pytest.mark.looptime
@pytest.mark.parametrize("departure", ["room_removed", "runtime_stop"])
async def test_leaving_cancels_a_pending_rejoin(harness, departure: str) -> None:
    await harness.join()
    [old] = harness.generations
    old.stop_gate.set()
    old.stop_errors.extend(RuntimeError("still running") for _ in range(2))
    await harness.leave()
    await harness.join()

    match departure:
        case "room_removed":
            await harness.leave()
        case "runtime_stop":
            await harness.runtime.stop()

    await elapse(RETRY_HORIZON_S)
    assert (old.stop_calls, harness.cleanups) == (3, [(ROOM, None)])
    assert (len(harness.generations), ROOM in harness.runtime.executions) == (1, False)


@pytest.mark.looptime
async def test_a_failed_cleanup_is_retried_before_the_room_is_recreated(
    harness,
) -> None:
    await harness.join()
    [old] = harness.generations
    old.stop_gate.set()
    harness.cleanup_errors.extend(
        RuntimeError("process still closing") for _ in range(2)
    )
    await harness.leave()

    await harness.join()

    assert (ROOM in harness.runtime.executions, len(harness.generations)) == (False, 1)
    await harness.until_live()
    assert old.stop_calls == 1, "a cleanup retry must not stop the execution again"
    assert harness.cleanups == [(ROOM, None)] * 3, (
        "no new execution before cleanup succeeds"
    )
    assert (len(harness.generations), harness.runtime.executions[ROOM]) == (
        2,
        harness.generations[1],
    )


@pytest.mark.looptime
async def test_a_failed_successor_build_is_retried_without_another_join(
    harness,
) -> None:
    await harness.joined_and_left()
    harness.build_errors.append(RuntimeError("factory unavailable"))

    await harness.join()
    await harness.until_live()

    assert harness.build_errors == [], "the failing build was attempted"
    assert (len(harness.generations), harness.runtime.executions[ROOM]) == (
        2,
        harness.generations[1],
    )


@pytest.mark.looptime
async def test_a_successor_that_fails_to_start_is_stopped_and_never_live(
    harness,
) -> None:
    await harness.joined_and_left()
    harness.start_errors.append(RuntimeError("harness did not start"))

    await harness.join()

    failed = harness.generations[1]
    assert ROOM not in harness.runtime.executions, "a failed start is never published"
    failed.stop_gate.set()
    await harness.until_live()
    assert failed.stop_calls == 1, "the failed candidate is released"
    assert (len(harness.generations), harness.runtime.executions[ROOM]) == (
        3,
        harness.generations[2],
    )
    assert harness.cleanups == [(ROOM, None), (ROOM, None)]


@pytest.mark.looptime
@pytest.mark.parametrize("departure", ["room_removed", "runtime_stop"])
async def test_leaving_while_a_successor_starts_releases_it(
    harness, departure: str
) -> None:
    await harness.joined_and_left()
    harness.hold_starts = True
    joining = asyncio.create_task(harness.join())
    await wait_for_condition(lambda: len(harness.generations) == 2, timeout=5.0)
    candidate = harness.generations[1]
    await asyncio.wait_for(candidate.start_entered.wait(), timeout=5.0)
    assert ROOM not in harness.runtime.executions
    candidate.stop_gate.set()

    match departure:
        case "room_removed":
            await harness.leave()
        case "runtime_stop":
            await harness.runtime.stop()
    await asyncio.wait_for(joining, timeout=5.0)

    await elapse(RETRY_HORIZON_S)
    assert (candidate.start_cancelled, candidate.stop_calls) == (True, 1)
    assert harness.cleanups == [(ROOM, None), (ROOM, None)], "old room, then candidate"
    assert len(harness.generations) == 2, "no successor after departure"
    assert harness.runtime.executions == {}


async def test_a_cancelled_runtime_stop_is_finished_by_the_next_stop(harness) -> None:
    await harness.join()
    [execution] = harness.generations
    stopping = asyncio.create_task(harness.runtime.stop())
    await execution.stop_entered.wait()

    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    execution.stop_gate.set()
    assert await asyncio.wait_for(harness.runtime.stop(), timeout=5.0) is True
    assert (execution.stop_calls, harness.cleanups) == (1, [(ROOM, None)])
    await harness.runtime.stop()
    assert (execution.stop_calls, harness.cleanups) == (1, [(ROOM, None)])
