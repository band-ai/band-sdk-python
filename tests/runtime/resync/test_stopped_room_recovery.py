"""A stopped room's probes: missed PLAY discovery and STOP fencing."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import ControlMode
from band.runtime.cycle import TurnScope
from band.runtime.scheduler import ResyncOutcome
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import (
    ROOM_ID,
    ResponseGate,
    rest_client_over,
)
from tests.runtime.resync.kit import ReconciledRoom


class TestStoppedRoomRecovery:
    """A stopped room's probes: missed PLAY discovery and STOP fencing."""

    @pytest.mark.looptime
    async def test_known_stop_recovers_after_missed_play(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)
        ctx.stop_room()
        peer.stopped = True
        peer.add_message("resumed")
        await asyncio.sleep(61)
        assert invoked == []
        peer.stopped = False
        await asyncio.sleep(60)
        assert invoked == ["resumed"]
        assert peer.marked("processed") == ["resumed"]

    @pytest.mark.looptime
    @pytest.mark.parametrize("mode", list(ControlMode))
    async def test_control_during_stopped_probe_invalidates_response(
        self, reconciled_room: ReconciledRoom, mode: ControlMode
    ) -> None:
        ctx, peer, invoked = reconciled_room
        ctx.stop_room()
        peer.add_message("obsolete")
        gate = ResponseGate(peer, "/next")
        async with rest_client_over(gate.answer) as rest:
            ctx.link.rest = rest
            recovery = asyncio.create_task(ctx.scheduler.run_pass(recovery=True))
            await gate.entered.wait()
            if mode is ControlMode.PLAY:
                await ctx.resume_room()
            else:
                ctx.interrupt(kind=mode)
            gate.release.set()
            assert await recovery is ResyncOutcome.BLOCKED
        assert invoked == []
        assert peer.requested("processing") == []

    @pytest.mark.looptime
    @pytest.mark.parametrize(
        "suffix", ["/pending/processed", "/obsolete/processing", "/context"]
    )
    async def test_new_stop_fences_recovered_candidate_across_awaits(
        self, reconciled_room: ReconciledRoom, suffix: str
    ) -> None:
        ctx, peer, invoked = reconciled_room
        ctx.stop_room()
        peer.add_message("obsolete")
        if suffix == "/pending/processed":
            ctx.claims.remember_ack_pending(ROOM_ID, "pending")
        if suffix == "/context":
            ctx.config.enable_context_hydration = True
        gate = ResponseGate(peer, suffix)
        async with rest_client_over(gate.answer) as rest:
            ctx.link.rest = rest
            recovery = asyncio.create_task(ctx.scheduler.run_pass(recovery=True))
            await gate.entered.wait()
            ctx.stop_room()
            peer.stopped = True
            gate.release.set()
            outcome = await recovery
            assert outcome in {ResyncOutcome.BLOCKED, ResyncOutcome.STOPPED}
        assert invoked == []
        assert ctx.is_stopped
        assert "obsolete" not in peer.marked("processed")

    @pytest.mark.looptime
    async def test_stopped_ack_does_not_block_missed_play_discovery(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)
        ctx.claims.remember_ack_pending(ROOM_ID, "completed")
        ctx.stop_room()
        peer.stopped = True
        peer.add_message("completed")
        await asyncio.sleep(61)
        assert ctx.claims.is_ack_pending(ROOM_ID, "completed")
        assert peer.requested("processed") == []
        peer.stopped = False
        await asyncio.sleep(60)
        assert peer.marked("processed") == ["completed"]
        assert invoked == []

    @pytest.mark.looptime
    async def test_stopped_probe_keeps_pause_on_nonrunnable_head(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        ctx.claims.remember_completed(ROOM_ID, "stuck")
        peer.add_message("stuck")
        ctx.stop_room()
        peer.stopped = True
        outcome = await ctx.scheduler.run_pass(recovery=True)
        assert outcome is ResyncOutcome.STOPPED
        assert ctx.is_stopped
        assert invoked == []

    @pytest.mark.looptime
    async def test_new_stop_during_observer_drain_keeps_candidate_paused(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        ctx.stop_room()
        peer.add_message("obsolete")
        cancelling = asyncio.Event()
        release = asyncio.Event()

        async def observer() -> None:
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()

        task = asyncio.create_task(observer())
        await asyncio.sleep(0)
        task.cancel()
        await cancelling.wait()
        ctx.current_scope = TurnScope(ctx._control_revision, observer_task=task)
        recovery = asyncio.create_task(ctx.scheduler.run_pass(recovery=True))
        await asyncio.sleep(0)
        ctx.stop_room()
        peer.stopped = True
        release.set()
        assert await recovery is ResyncOutcome.STOPPED
        await task
        assert invoked == []
        assert peer.requested("processing") == []
