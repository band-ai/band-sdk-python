"""``SessionConfig.release_idle_room_after_s``: idle room resource release.

Each wait is on an event the code under test sets (a turn starting, the
release callback running), never a bare sleep standing in for progress.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext, ExecutionState
from band.runtime.runtime import AgentRuntime
from band.runtime.types import SessionConfig
from tests.adapters.test_claude_sdk_idle_release import ROOM, claude_room
from tests.adapters.test_codex_adapter import (
    FakeCodexClient,
    _bootstrap_turn,
    _turn_completed,
    make_codex_adapter,
)
from tests.conftest import make_room_added_event, make_room_removed_event
from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter
from tests.runtime.conftest import make_link_mock, platform_msg, wait_for_condition

_RELEASE_AFTER_S = 0.05


class Room:
    """One ExecutionContext driven through /next, with recorded releases."""

    def __init__(
        self,
        room_id: str = "room-1",
        *,
        release_after_s: float | None = _RELEASE_AFTER_S,
        release_error: Exception | None = None,
        adapter_release: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.link = make_link_mock()
        self.release_gate: asyncio.Event | None = None
        self.release_finished = False
        self.pending: list[Any] = []

        async def next_message(*_args: Any, **_kwargs: Any) -> Any:
            return self.pending.pop(0) if self.pending else None

        self.link.get_next_message.side_effect = next_message
        self.link.get_stale_processing_messages = AsyncMock(return_value=[])
        self.turns: list[str] = []
        self.turn_started = asyncio.Event()
        self.turn_gate: asyncio.Event | None = None
        self.releases: list[str] = []
        self.released = asyncio.Event()
        self._release_error = release_error
        self._adapter_release = adapter_release
        self.ctx = ExecutionContext(
            room_id,
            self.link,
            self._on_execute,
            config=SessionConfig(
                release_idle_room_after_s=release_after_s,
                idle_resync_seconds=30.0,
                enable_working_state=False,
            ),
            on_idle_release=self._on_release,
        )

    async def _on_execute(self, _ctx: Any, event: Any) -> None:
        self.turns.append(event.payload.id)
        self.turn_started.set()
        if self.turn_gate is not None:
            await self.turn_gate.wait()

    async def _on_release(self, room_id: str) -> None:
        self.releases.append(room_id)
        self.released.set()
        if self.release_gate is not None:
            await self.release_gate.wait()
            self.release_finished = True
        if self._release_error is not None:
            raise self._release_error
        if self._adapter_release is not None:
            await self._adapter_release(room_id)

    async def send(self, msg_id: str) -> None:
        """Deliver one message through /next and wait until its turn starts."""
        self.turn_started.clear()
        self.pending.append(platform_msg(msg_id))
        await self.ctx.request_resync()
        await asyncio.wait_for(self.turn_started.wait(), timeout=5.0)

    async def wait_released(self) -> None:
        await asyncio.wait_for(self.released.wait(), timeout=5.0)
        self.released.clear()


@pytest.fixture
async def room():
    rooms: list[Room] = []

    def make(**kwargs: Any) -> Room:
        rooms.append(Room(**kwargs))
        return rooms[-1]

    yield make
    for r in rooms:
        await r.ctx.stop()


async def _started(r: Room) -> Room:
    await r.ctx.start()
    return r


async def test_an_idle_room_is_released_once_after_a_turn(room) -> None:
    r = await _started(room())
    await r.send("msg-1")

    await r.wait_released()

    assert r.releases == ["room-1"]
    assert r.ctx.state is ExecutionState.IDLE


async def test_the_next_turn_rearms_the_release(room) -> None:
    r = await _started(room())
    await r.send("msg-1")
    await r.wait_released()

    await r.send("msg-2")
    await r.wait_released()

    assert (r.turns, r.releases) == (["msg-1", "msg-2"], ["room-1", "room-1"])


async def test_a_room_that_never_ran_a_turn_is_left_alone(room) -> None:
    r = await _started(room())
    other = await _started(room(room_id="room-2"))
    await other.send("msg-1")
    await other.wait_released()

    assert (r.releases, other.releases) == ([], ["room-2"])


async def test_a_running_turn_is_never_released(room) -> None:
    r = await _started(room())
    r.turn_gate = asyncio.Event()
    await r.send("msg-1")

    # Several release periods pass mid-turn; the loop is busy with the turn,
    # so no release can be scheduled until it ends.
    await asyncio.sleep(_RELEASE_AFTER_S * 4)
    assert (r.ctx.state, r.releases) == (ExecutionState.PROCESSING, [])

    r.turn_gate.set()
    await r.wait_released()
    assert r.releases == ["room-1"]


async def test_a_message_queued_at_expiry_runs_before_any_release(room) -> None:
    r = await _started(room())
    r.turn_gate = asyncio.Event()
    await r.send("msg-1")
    r.turn_started.clear()
    r.pending.append(platform_msg("msg-2"))
    await r.ctx.request_resync()

    r.turn_gate.set()
    await asyncio.wait_for(r.turn_started.wait(), timeout=5.0)
    await r.wait_released()

    assert (r.turns, r.releases) == (["msg-1", "msg-2"], ["room-1"])


async def test_unset_config_never_releases(room) -> None:
    r = await _started(room(release_after_s=None))
    await r.send("msg-1")

    assert r.ctx._next_idle_wait() == (30.0, False)
    assert r.releases == []


async def test_a_failed_release_leaves_the_room_serving(room) -> None:
    r = await _started(room(release_error=RuntimeError("close failed")))
    await r.send("msg-1")
    await r.wait_released()

    await r.send("msg-2")

    assert r.turns == ["msg-1", "msg-2"]
    assert r.ctx.is_running


async def test_stop_before_expiry_leaves_no_pending_release(room) -> None:
    r = await _started(room(release_after_s=30.0))
    await r.send("msg-1")

    await r.ctx.stop()

    assert (r.ctx.is_running, r.releases) == (False, [])


def test_a_non_positive_setting_is_refused() -> None:
    with pytest.raises(ValueError, match="release_idle_room_after_s"):
        SessionConfig(release_idle_room_after_s=0)


async def test_stop_during_a_release_waits_for_its_teardown(room) -> None:
    r = await _started(room())
    r.release_gate = asyncio.Event()
    await r.send("msg-1")
    await r.wait_released()

    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
    for _ in range(5):
        await asyncio.sleep(0)
    assert not stopping.done(), "stop must wait for the pending teardown"

    r.release_gate.set()
    await asyncio.wait_for(stopping, timeout=5.0)

    assert (r.release_finished, r.ctx.is_running) == (True, False)


async def test_cancelling_stop_mid_release_keeps_the_teardown_running(room) -> None:
    r = await _started(room())
    r.release_gate = asyncio.Event()
    await r.send("msg-1")
    await r.wait_released()
    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)

    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    assert r.ctx._release_task is not None, "the teardown must stay tracked"
    r.release_gate.set()
    await asyncio.wait_for(r.ctx.stop(), timeout=5.0)
    assert (r.release_finished, r.ctx._release_task) == (True, None)


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


async def test_a_cancelled_runtime_stop_is_finished_by_the_next_stop(room) -> None:
    r = room()
    r.release_gate = asyncio.Event()
    cleanups: list[str] = []
    runtime = _runtime_over(r, cleanups)
    await runtime._create_execution(ROOM)
    await r.send("msg-1")
    await r.wait_released()
    stopping = asyncio.create_task(runtime.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)

    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    r.release_gate.set()
    await asyncio.wait_for(runtime.stop(), timeout=5.0)

    assert (r.release_finished, cleanups) == (True, [ROOM])


async def test_a_release_failing_after_its_stop_was_cancelled_is_still_seen(
    room, caplog
) -> None:
    r = room(release_error=RuntimeError("teardown failed"))
    r.release_gate = asyncio.Event()
    cleanups: list[str] = []
    runtime = _runtime_over(r, cleanups)
    await runtime._create_execution(ROOM)
    await r.send("msg-1")
    await r.wait_released()
    stopping = asyncio.create_task(runtime.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    r.release_gate.set()
    await asyncio.wait_for(runtime.stop(), timeout=5.0)

    failures = [
        rec for rec in caplog.records if "idle resource release failed" in rec.message
    ]
    assert [str(rec.exc_info[1]) for rec in failures] == ["teardown failed"]
    assert cleanups == [ROOM]


class GatedCloseCodexClient(FakeCodexClient):
    """A Codex client whose close() waits for the test to let it finish."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.close_entered = asyncio.Event()
        self.close_gate = asyncio.Event()

    async def close(self) -> None:
        self.close_entered.set()
        await self.close_gate.wait()
        await super().close()


async def test_stopping_mid_codex_release_still_closes_the_app_server(room) -> None:
    client = GatedCloseCodexClient(events=[_turn_completed()])
    adapter = make_codex_adapter(client)
    await _bootstrap_turn(adapter)
    r = await _started(
        room(room_id="room-1", adapter_release=adapter.release_room_resources)
    )
    await r.send("msg-1")
    await asyncio.wait_for(client.close_entered.wait(), timeout=5.0)

    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
    client.close_gate.set()
    await asyncio.wait_for(stopping, timeout=5.0)

    room_client = adapter._room_clients["room-1"]
    assert (client.closed, room_client.client) == (True, None)
    assert adapter._released_threads == {"room-1": "thr-1"}


async def test_stopping_mid_omp_release_still_stops_the_agent_process(room) -> None:
    agent = FakeACPAgent(supports_session_load=True).will_say("Noted.")
    async with acp_adapter(agent) as session:
        await session.send("My favorite color is blue.", bootstrap=True)
        runtime = session.adapter._runtimes["room-1"]
        stop_entered, stop_gate = asyncio.Event(), asyncio.Event()
        stop_completed = False
        real_stop = runtime.stop

        async def gated_stop() -> None:
            nonlocal stop_completed
            stop_entered.set()
            await stop_gate.wait()
            await real_stop()
            stop_completed = True

        runtime.stop = gated_stop  # type: ignore[method-assign]
        r = await _started(
            room(adapter_release=session.adapter._release_loadable_session)
        )
        await r.send("msg-1")
        await asyncio.wait_for(stop_entered.wait(), timeout=5.0)

        stopping = asyncio.create_task(r.ctx.stop())
        await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
        stop_gate.set()
        await asyncio.wait_for(stopping, timeout=5.0)

        assert stop_completed
        assert "room-1" not in session.adapter._runtimes


async def test_stopping_mid_claude_release_still_stops_the_session(
    room, tmp_path
) -> None:
    claude = await claude_room(str(tmp_path))
    claude.manager.cleanup_gate = asyncio.Event()
    r = await _started(room(adapter_release=claude.adapter.release_room_resources))
    await r.send("msg-1")
    await asyncio.wait_for(claude.manager.cleanup_entered.wait(), timeout=5.0)

    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
    claude.manager.cleanup_gate.set()
    await asyncio.wait_for(stopping, timeout=5.0)

    assert claude.manager.has_session(ROOM) is False
    assert claude.adapter._released_sessions == {ROOM: "sess-1"}
    await claude.adapter.cleanup_all()


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
