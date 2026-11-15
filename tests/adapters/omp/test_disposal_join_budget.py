"""Caller waits on room disposal are bounded; the unfinished disposal stays owned."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.exceptions import CleanupIncompleteError
from band.testing import FakeAgentTools
from tests.adapters.omp.custody import room_runtime
from tests.adapters.omp.lifecyclekit import HeldCloseRuntime, Launches, failing_runtime
from tests.adapters.omp.models import RecordingRuntime
from tests.adapters.omp.resolution import cross_deadline
from tests.adapters.omp.turns import run_turn
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

BUDGET_S = 0.2
PAST_BUDGET_S = 0.4


async def admission_is_refused(adapter: OmpAdapter) -> bool:
    """Whether a new turn is turned away while the room's disposal is unfinished."""
    try:
        await run_turn(adapter, FakeAgentTools())
    except asyncio.CancelledError:
        return True
    return False


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestDisposalJoinBudget:
    @pytest.mark.asyncio
    async def test_failed_turn_stops_waiting_for_a_resistant_close_then_recovers(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        closing, finish_close = asyncio.Event(), asyncio.Event()
        resistant = failing_runtime(closing, finish_close)
        launches.runtime_factory = lambda: resistant  # type: ignore[assignment,return-value]
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=BUDGET_S), emit=()
        )
        turn: asyncio.Task[None] | None = None
        try:
            async with controlled_clock() as clock:
                turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
                await closing.wait()

                await cross_deadline(clock, PAST_BUDGET_S)

                with pytest.raises(CleanupIncompleteError):
                    await turn
                assert not resistant.closed
                assert await admission_is_refused(adapter)
                assert len(launches.runtimes) == 1

                finish_close.set()
                await adapter.on_cleanup("room-123")
                launches.runtime_factory = RecordingRuntime
                tools = FakeAgentTools()
                await run_turn(adapter, tools)

                assert resistant.closed and len(launches.runtimes) == 2
                assert tools.turn.replied
        finally:
            finish_close.set()
            if turn is not None:
                await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_cancelled_turn_keeps_its_cancellation_when_the_close_resists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        resistant = HeldCloseRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: resistant
        )
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=BUDGET_S), emit=()
        )
        turn: asyncio.Task[None] | None = None
        try:
            async with controlled_clock() as clock:
                turn = asyncio.create_task(run_turn(adapter, FakeAgentTools()))
                await resistant.yielded.wait()
                turn.cancel()
                await resistant.closing.arrived()

                await cross_deadline(clock, PAST_BUDGET_S)

                with pytest.raises(asyncio.CancelledError):
                    await turn
                assert await admission_is_refused(adapter)
        finally:
            resistant.closing.released.set()
            if turn is not None:
                await asyncio.gather(turn, return_exceptions=True)
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cleanup_stops_waiting_for_a_resistant_close_then_recovers(
        self, launches: Launches, tmp_path: Path
    ) -> None:
        resistant = HeldCloseRuntime()
        launches.runtime_factory = lambda: resistant
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=BUDGET_S), emit=()
        )
        cleanup: asyncio.Task[None] | None = None
        try:
            async with controlled_clock() as clock:
                await run_turn(adapter, FakeAgentTools())
                cleanup = asyncio.create_task(adapter.on_cleanup("room-123"))
                await resistant.closing.arrived()

                await cross_deadline(clock, PAST_BUDGET_S)

                with pytest.raises(CleanupIncompleteError):
                    await cleanup
                assert room_runtime(adapter, "room-123") is resistant
                assert await admission_is_refused(adapter)
                assert len(launches.runtimes) == 1

                resistant.closing.released.set()
                await adapter.on_cleanup("room-123")
                assert resistant.close_calls == 1 and resistant.closed
                launches.runtime_factory = RecordingRuntime
                tools = FakeAgentTools()
                await run_turn(adapter, tools)

                assert len(launches.runtimes) == 2
                assert tools.turn.replied
        finally:
            resistant.closing.released.set()
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)
            await adapter.cleanup_all()
