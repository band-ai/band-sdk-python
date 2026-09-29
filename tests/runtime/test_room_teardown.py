"""Room teardown stays owned until stop and cleanup both succeed.

One teardown task per room, a rejoin waits for the predecessor, and a
cancelled stop is finished by the next stop. None of this depends on idle
resource release.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from band.runtime.types import SessionConfig
from tests.conftest import make_room_added_event, make_room_removed_event
from tests.runtime.conftest import make_link_mock, platform_msg, wait_for_condition

ROOM = "room-1"


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
            self._on_execute,
            config=SessionConfig(
                idle_resync_seconds=30.0,
                enable_working_state=False,
            ),
        )

    async def _on_execute(self, _ctx: Any, _event: Any) -> None:
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
    await asyncio.sleep(0)  # stop() runs until it waits on the cancelled loop
    assert r.ctx._process_loop_task is not None
    assert not r.ctx._process_loop_task.done()

    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stopping, timeout=5.0)

    await asyncio.wait_for(r.ctx.stop(), timeout=5.0)
    assert r.ctx.is_running is False


def _runtime_over(r: Room, cleanups: list[str]) -> AgentRuntime:
    """An AgentRuntime whose one room runs ``r``'s execution context."""

    async def cleanup(room_id: str) -> None:
        cleanups.append(room_id)

    return AgentRuntime(
        r.link,
        "agent-1",
        AsyncMock(),
        execution_factory=lambda *_args, **_kwargs: r.ctx,
        on_session_cleanup=cleanup,
    )


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


async def test_overlapping_destroys_share_one_stop_and_one_cleanup() -> None:
    g = GenerationRuntime()
    await g.runtime._create_execution(ROOM)
    [first] = g.generations

    callers = [
        asyncio.create_task(g.runtime._destroy_execution(ROOM)) for _ in range(2)
    ]
    await first.stop_entered.wait()
    first.stop_gate.set()
    results = await asyncio.gather(*callers)

    assert (first.stop_calls, g.cleanups, results) == (1, [(ROOM, None)], [True, True])


async def test_a_rejoin_waits_for_the_previous_rooms_cleanup() -> None:
    g = GenerationRuntime()
    await g.runtime._create_execution(ROOM)
    [old] = g.generations
    leaving = asyncio.create_task(g.runtime._destroy_execution(ROOM))
    await old.stop_entered.wait()
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving

    rejoining = asyncio.create_task(g.runtime._create_execution(ROOM))
    for _ in range(5):
        await asyncio.sleep(0)
    assert (rejoining.done(), len(g.generations)) == (False, 1)
    old.stop_gate.set()
    new = await asyncio.wait_for(rejoining, timeout=5.0)

    assert g.cleanups == [(ROOM, None)], "old cleanup never saw the new room"
    assert (old.stop_calls, g.runtime.executions[ROOM]) == (1, new)


async def test_a_failed_stop_keeps_the_room_until_a_retry_stops_it() -> None:
    g = GenerationRuntime()
    await g.runtime._create_execution(ROOM)
    [old] = g.generations
    old.stop_gate.set()
    old.stop_errors.append(RuntimeError("process still running"))

    first = await g.runtime._destroy_execution(ROOM)

    assert (first, old.stop_calls, g.cleanups) == (False, 1, [])
    assert g.runtime._teardowns[ROOM].execution is old
    second = await g.runtime._destroy_execution(ROOM)
    assert (second, old.stop_calls, g.cleanups) == (True, 2, [(ROOM, None)])


async def test_a_rejoin_past_a_failing_stop_is_created_once_the_stop_succeeds() -> None:
    g = GenerationRuntime()
    g.runtime._teardown_retry_delay_s = 0.0
    link = g.runtime.link
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()
    link.is_room_subscribed = MagicMock(return_value=True)
    presence = g.runtime.presence
    await presence._handle_room_added(make_room_added_event(room_id=ROOM))
    [old] = g.generations
    old.stop_gate.set()
    old.stop_errors.extend(RuntimeError("still running") for _ in range(3))
    await presence._handle_room_removed(make_room_removed_event(room_id=ROOM))

    await presence._handle_room_added(make_room_added_event(room_id=ROOM))

    assert ROOM in presence.roster.tracked_room_ids()
    assert (ROOM in g.runtime.executions, g.cleanups) == (False, [])
    await asyncio.wait_for(_recovery(g), timeout=5.0)
    assert (old.stop_calls, g.cleanups) == (4, [(ROOM, None)])
    assert (len(g.generations), g.runtime.executions[ROOM]) == (2, g.generations[1])


def _admit_rooms(link: Any) -> None:
    """Let RoomPresence's admission subscribe on a mock link."""
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()
    link.is_room_subscribed = MagicMock(return_value=True)


async def test_a_room_removal_preempts_a_graceful_shutdown(room) -> None:
    r = room()
    r.turn_gate = asyncio.Event()
    cleanups: list[str] = []
    runtime = _runtime_over(r, cleanups)
    _admit_rooms(r.link)
    await runtime.presence._handle_room_added(make_room_added_event(room_id=ROOM))
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

    r.ctx.stop = tracked_stop  # type: ignore[method-assign]
    shutting_down = asyncio.create_task(runtime.stop(timeout=30.0))
    await wait_for_condition(lambda: stop_timeouts == [30.0], timeout=5.0)

    await asyncio.wait_for(
        runtime.presence._handle_room_removed(make_room_removed_event(room_id=ROOM)),
        timeout=5.0,
    )

    assert not r.turn_gate.is_set()
    assert (cleanups, most_concurrent_stops, stop_timeouts) == ([ROOM], 1, [30.0, None])
    assert await asyncio.wait_for(shutting_down, timeout=5.0) is False


@pytest.mark.parametrize("departure", ["room_removed", "runtime_stop"])
async def test_leaving_cancels_a_pending_rejoin(departure: str) -> None:
    g = GenerationRuntime()
    g.runtime._teardown_retry_delay_s = 3600.0
    _admit_rooms(g.runtime.link)
    presence = g.runtime.presence
    await presence._handle_room_added(make_room_added_event(room_id=ROOM))
    [old] = g.generations
    old.stop_gate.set()
    old.stop_errors.extend(RuntimeError("still running") for _ in range(2))
    await presence._handle_room_removed(make_room_removed_event(room_id=ROOM))
    await presence._handle_room_added(make_room_added_event(room_id=ROOM))
    pending = _recovery(g)

    match departure:
        case "room_removed":
            await presence._handle_room_removed(make_room_removed_event(room_id=ROOM))
        case "runtime_stop":
            await g.runtime.stop()

    assert (pending.cancelled(), g.runtime._pending_creations) == (True, {})
    assert (old.stop_calls, g.cleanups) == (3, [(ROOM, None)])
    assert (len(g.generations), ROOM in g.runtime.executions) == (1, False)


async def test_a_failed_cleanup_is_retried_before_the_room_is_recreated() -> None:
    g = GenerationRuntime()
    g.runtime._teardown_retry_delay_s = 0.0
    _admit_rooms(g.runtime.link)
    presence = g.runtime.presence
    await presence._handle_room_added(make_room_added_event(room_id=ROOM))
    [old] = g.generations
    old.stop_gate.set()
    g.cleanup_errors.extend(RuntimeError("process still closing") for _ in range(2))
    await presence._handle_room_removed(make_room_removed_event(room_id=ROOM))

    await presence._handle_room_added(make_room_added_event(room_id=ROOM))

    assert (ROOM in g.runtime.executions, len(g.generations)) == (False, 1)
    await asyncio.wait_for(_recovery(g), timeout=5.0)
    assert old.stop_calls == 1, "a cleanup retry must not stop the execution again"
    assert g.cleanups == [(ROOM, None)] * 3, "no new execution before cleanup succeeds"
    assert (len(g.generations), g.runtime.executions[ROOM]) == (2, g.generations[1])


def _recovery(g: GenerationRuntime) -> asyncio.Task[None]:
    task = g.runtime._pending_creations[ROOM].task
    assert task is not None
    return task


async def _leave_and_rejoin(g: GenerationRuntime) -> GatedExecution:
    g.runtime._teardown_retry_delay_s = 0.0
    _admit_rooms(g.runtime.link)
    presence = g.runtime.presence
    await presence._handle_room_added(make_room_added_event(room_id=ROOM))
    [old] = g.generations
    old.stop_gate.set()
    await presence._handle_room_removed(make_room_removed_event(room_id=ROOM))
    return old


async def test_a_failed_successor_build_is_retried_without_another_join() -> None:
    g = GenerationRuntime()
    await _leave_and_rejoin(g)
    g.build_errors.append(RuntimeError("factory unavailable"))

    await g.runtime.presence._handle_room_added(make_room_added_event(room_id=ROOM))
    await wait_for_condition(lambda: ROOM in g.runtime.executions, timeout=5.0)

    assert g.build_errors == [], "the failing build was attempted"
    assert (len(g.generations), g.runtime.executions[ROOM]) == (2, g.generations[1])


async def test_a_successor_that_fails_to_start_is_stopped_and_never_live() -> None:
    g = GenerationRuntime()
    await _leave_and_rejoin(g)
    g.start_errors.append(RuntimeError("harness did not start"))

    await g.runtime.presence._handle_room_added(make_room_added_event(room_id=ROOM))

    failed = g.generations[1]
    assert ROOM not in g.runtime.executions, "a failed start is never published"
    failed.stop_gate.set()
    await asyncio.wait_for(_recovery(g), timeout=5.0)
    assert failed.stop_calls == 1, "the failed candidate is released"
    assert (len(g.generations), g.runtime.executions[ROOM]) == (3, g.generations[2])
    assert g.cleanups == [(ROOM, None), (ROOM, None)]


@pytest.mark.parametrize("departure", ["room_removed", "runtime_stop"])
async def test_leaving_while_a_successor_starts_releases_it(departure: str) -> None:
    g = GenerationRuntime()
    await _leave_and_rejoin(g)
    g.hold_starts = True
    presence = g.runtime.presence
    joining = asyncio.create_task(
        presence._handle_room_added(make_room_added_event(room_id=ROOM))
    )
    await wait_for_condition(lambda: len(g.generations) == 2, timeout=5.0)
    candidate = g.generations[1]
    await asyncio.wait_for(candidate.start_entered.wait(), timeout=5.0)
    assert ROOM not in g.runtime.executions
    candidate.stop_gate.set()

    match departure:
        case "room_removed":
            await presence._handle_room_removed(make_room_removed_event(room_id=ROOM))
        case "runtime_stop":
            await g.runtime.stop()
    await asyncio.wait_for(joining, timeout=5.0)

    assert (candidate.start_cancelled, candidate.stop_calls) == (True, 1)
    assert g.cleanups == [(ROOM, None), (ROOM, None)], "old room, then candidate"
    assert len(g.generations) == 2, "no successor after departure"
    runtime = g.runtime
    assert (runtime.executions, runtime._teardowns, runtime._pending_creations) == (
        {},
        {},
        {},
    )


async def test_a_cancelled_runtime_stop_is_finished_by_the_next_stop() -> None:
    g = GenerationRuntime()
    await g.runtime._create_execution(ROOM)
    [execution] = g.generations
    stopping = asyncio.create_task(g.runtime.stop())
    await execution.stop_entered.wait()

    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    assert ROOM in g.runtime._teardowns
    execution.stop_gate.set()
    await asyncio.wait_for(g.runtime.stop(), timeout=5.0)
    assert g.cleanups == [(ROOM, None)]
    assert ROOM not in g.runtime._teardowns
