from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.custody import room_runtime
from tests.adapters.omp.lifecyclekit import (
    HeldCloseRuntime,
    Launches,
    SignallingCloseRuntime,
    SignallingLock,
    failing_runtime,
)
from tests.adapters.omp.models import RecordingRuntime
from tests.adapters.omp.resolution import (
    CleanupObservedAdapter,
    SettlingResolver,
    remote,
    settling_adapter,
    start_blocked_turn,
    start_cleanup,
)
from tests.adapters.omp.runtime import PendingRuntime
from tests.adapters.omp.turns import run_turn


class TestRoomRetirement:
    @pytest.mark.asyncio
    async def test_workspace_is_exclusive_until_cancelled_resolution_settles(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        resolver = SettlingResolver()
        adapter = CleanupObservedAdapter(
            workspace_for_room=lambda _room: str(tmp_path),
            resolve_mcp_servers=resolver,
            emit=(),
        )
        turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        try:
            await resolver.entered.wait()
            turn.cancel()
            await resolver.cancel_seen.wait()
            with pytest.raises(ValueError, match="assigned .* to both"):
                await run_turn(adapter, FakeAgentTools(), room_id="competing-room")
            assert not turn.done() and launches.runtimes == []
            resolver.settle.set()
            with pytest.raises(asyncio.CancelledError):
                await turn
            tools = FakeAgentTools()
            await run_turn(adapter, tools, room_id="competing-room")
            assert tools.turn.replied and len(launches.runtimes) == 1
        finally:
            resolver.settle.set()
            await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_input_entering_at_physical_close_cannot_reopen_the_room(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        runtime = SignallingCloseRuntime()
        launches.runtime_factory = lambda: runtime
        adapter = CleanupObservedAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        await run_turn(adapter, FakeAgentTools())

        async def arrive_at_close() -> None:
            await runtime.physical_close.wait()
            with pytest.raises(asyncio.CancelledError):
                await run_turn(adapter, FakeAgentTools())

        arrival = asyncio.create_task(arrive_at_close())
        try:
            await adapter.on_cleanup("room-123")
            await arrival
            assert runtime.closed and len(launches.runtimes) == 1
            launches.runtime_factory = RecordingRuntime
            await run_turn(adapter, FakeAgentTools())
            assert len(launches.runtimes) == 2
        finally:
            arrival.cancel()
            await asyncio.gather(arrival, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cleanup_settles_a_turn_queued_behind_a_blocked_resolution(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SettlingResolver()
        resolver.settle.set()

        adapter = CleanupObservedAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolver, emit=()
        )
        room_lock = SignallingLock()
        adapter._locks["room-123"] = room_lock
        first = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        await asyncio.wait_for(resolver.entered.wait(), 1)
        queued = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
        await asyncio.wait_for(
            room_lock.contended.wait(), 1
        )  # queued waits on the lock

        await asyncio.wait_for(adapter.cleanup_all(), 1)

        assert first.done() and queued.done()
        assert resolver.calls == 1 and launches.runtimes == []
        assert not adapter._workspaces.rooms
        for task in (first, queued):
            with pytest.raises(asyncio.CancelledError):
                await task
        await run_turn(adapter, FakeAgentTools())  # a later explicit turn recovers
        assert resolver.calls == 2 and len(launches.runtimes) == 1
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_a_turn_arriving_during_retirement_is_refused(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SettlingResolver()
        adapter = settling_adapter(tmp_path, resolver)
        first = await start_blocked_turn(adapter, resolver)
        cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
        await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)
        assert resolver.calls == 1 and launches.runtimes == []

        resolver.settle.set()
        await asyncio.wait_for(cleanup, 1)
        with pytest.raises(asyncio.CancelledError):
            await first
        assert resolver.cancellations == 1
        await run_turn(adapter, FakeAgentTools())  # admission reopens after retirement
        assert resolver.calls == 2 and len(launches.runtimes) == 1
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_overlapping_cleanups_cancel_the_settling_turn_once(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SettlingResolver()
        adapter = settling_adapter(tmp_path, resolver)
        first = await start_blocked_turn(adapter, resolver)
        room_cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
        await asyncio.wait_for(resolver.cancel_seen.wait(), 1)
        shutdown = await start_cleanup(adapter, shutdown=True)
        # Shutdown stays joined to the running retirement, not finishing or re-cancelling.

        assert not shutdown.done() and resolver.cancellations == 1
        resolver.settle.set()
        await asyncio.wait_for(asyncio.gather(room_cleanup, shutdown), 1)

        assert resolver.cancellations == 1 and resolver.settled
        assert launches.runtimes == [] and first.cancelled()

    @pytest.mark.asyncio
    async def test_cancelling_a_cleanup_waiter_leaves_retirement_running(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SettlingResolver()
        adapter = settling_adapter(tmp_path, resolver)
        first = await start_blocked_turn(adapter, resolver)
        waiter = asyncio.create_task(adapter.on_cleanup("room-123"))
        await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert resolver.cancellations == 1
        with pytest.raises(asyncio.CancelledError):  # admission stays closed
            await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)
        resolver.settle.set()
        await asyncio.wait_for(adapter.cleanup_all(), 1)  # joins the same retirement
        assert resolver.cancellations == 1
        assert first.cancelled() and launches.runtimes == []

    @pytest.mark.asyncio
    async def test_cleanup_all_joins_a_retirement_blocked_in_native_close(
        self, launches: Launches, tmp_path: Path
    ):
        runtime = HeldCloseRuntime()
        launches.runtime_factory = lambda: runtime
        adapter = CleanupObservedAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        await run_turn(adapter, FakeAgentTools())  # a settled turn leaves a live room
        room_cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
        await runtime.closing.arrived()
        shutdown = asyncio.create_task(adapter.cleanup_all())
        with pytest.raises(asyncio.CancelledError):  # no new turn during teardown
            await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)

        assert not shutdown.done() and "room-123" in adapter._workspaces.rooms
        runtime.closing.released.set()
        await asyncio.wait_for(asyncio.gather(room_cleanup, shutdown), 1)

        assert runtime.close_calls == 1 and runtime.closed
        assert len(launches.runtimes) == 1 and not adapter._workspaces.rooms
        launches.runtime_factory = RecordingRuntime
        await run_turn(adapter, FakeAgentTools())
        assert len(launches.runtimes) == 2
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cleanup_joins_a_turn_already_settling_an_external_cancellation(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SettlingResolver()
        adapter = settling_adapter(tmp_path, resolver)
        first = await start_blocked_turn(adapter, resolver)
        first.cancel()
        await asyncio.wait_for(resolver.cancel_seen.wait(), 1)

        cleanup = await start_cleanup(adapter)

        assert not cleanup.done()
        assert resolver.cancellations == 1
        resolver.settle.set()
        await asyncio.wait_for(cleanup, 1)
        assert resolver.cancellations == 1
        assert first.cancelled() and launches.runtimes == []
        await run_turn(adapter, FakeAgentTools())  # explicit recovery
        assert len(launches.runtimes) == 1
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cleanup_requested_by_the_resolving_turn_launches_nothing(
        self, launches: Launches, tmp_path: Path
    ):
        adapter: OmpAdapter

        async def resolve(_workspace: str):
            if not launches.runtimes and resolve.calls == 0:
                resolve.calls += 1
                await adapter.on_cleanup("room-123")  # the room's own turn retires it
            return {"linear": remote("late")}

        resolve.calls = 0  # type: ignore[attr-defined]
        adapter = CleanupObservedAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_mcp_servers=resolve, emit=()
        )

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_turn(adapter, FakeAgentTools()), 1)
        await asyncio.wait_for(adapter.cleanup_all(), 1)

        assert launches.runtimes == [] and not adapter._workspaces.rooms
        await run_turn(adapter, FakeAgentTools())  # explicit recovery
        assert len(launches.runtimes) == 1
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_retirement_waits_for_the_turn_not_for_its_callers_other_work(
        self, launches: Launches, tmp_path: Path
    ):
        resolver = SettlingResolver()
        resolver.settle.set()  # cancellation settles immediately
        adapter = settling_adapter(tmp_path, resolver)
        turn_returned = asyncio.Event()

        async def caller():
            try:
                await run_turn(adapter, FakeAgentTools())
            except asyncio.CancelledError:
                turn_returned.set()
            await asyncio.Event().wait()  # unrelated work keeps the caller alive

        wrapper = asyncio.create_task(caller())
        await asyncio.wait_for(resolver.entered.wait(), 1)

        await asyncio.wait_for(adapter.on_cleanup("room-123"), 1)

        await asyncio.wait_for(turn_returned.wait(), 1)
        assert not wrapper.done()
        assert resolver.cancellations == 1
        assert launches.runtimes == []
        wrapper.cancel()
        await run_turn(adapter, FakeAgentTools())  # explicit recovery
        assert len(launches.runtimes) == 1
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cleanup_all_joins_a_retirement_with_no_room_or_turn_left(
        self, launches: Launches, tmp_path: Path
    ):
        adapter = CleanupObservedAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        room_lock = SignallingLock()
        adapter._locks["room-123"] = room_lock
        await room_lock.acquire()  # another holder keeps the retirement from closing
        retirement = asyncio.create_task(adapter.on_cleanup("room-123"))
        await asyncio.wait_for(room_lock.contended.wait(), 1)
        assert room_runtime(adapter, "room-123") is None

        shutdown = await start_cleanup(adapter, shutdown=True)

        assert not shutdown.done()  # it joined the in-flight retirement
        room_lock.release()
        await asyncio.wait_for(asyncio.gather(retirement, shutdown), 1)
        assert launches.runtimes == []

    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_retirement_never_cancels_work_after_the_captured_turn_finished(
        self, tmp_path: Path, monkeypatch
    ):
        adapter = CleanupObservedAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        unrelated = asyncio.Event()
        continued = asyncio.Event()
        runtime = PendingRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **k: runtime
        )

        async def caller():
            await run_turn(adapter, FakeAgentTools())
            await unrelated.wait()  # the caller's own work after its turn
            continued.set()

        wrapper = asyncio.create_task(caller())
        await asyncio.wait_for(runtime.yielded.wait(), 1)
        # Cleanup captures the live turn; the turn then finishes before retirement runs.
        cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
        runtime.finish.set()
        await asyncio.wait_for(cleanup, 1)

        assert not wrapper.cancelled() and not wrapper.done()
        assert runtime.closed and not adapter._workspaces.rooms
        unrelated.set()
        await asyncio.wait_for(wrapper, 1)
        assert continued.is_set()

    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_cleanup_joins_outcome_handling_after_the_deadline_context_exited(
        self, launches: Launches, tmp_path: Path, monkeypatch
    ):
        closing = asyncio.Event()
        finish_close = asyncio.Event()

        runtime = failing_runtime(closing, finish_close)
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **k: runtime
        )
        adapter = CleanupObservedAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FakeAgentTools()
        turn = asyncio.create_task(run_turn(adapter, tools))
        await asyncio.wait_for(
            closing.wait(), 1
        )  # the turn is handling its own failure

        cleanup = await start_cleanup(adapter)
        assert not cleanup.done()
        finish_close.set()
        await asyncio.wait_for(cleanup, 1)

        with pytest.raises(RuntimeError, match="native turn failed"):
            await turn
        assert [failure["provider"] for failure in reported_failures(tools)] == ["omp"]
        assert runtime.closed and not adapter._workspaces.rooms
