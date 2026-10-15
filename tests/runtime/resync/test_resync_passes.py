"""Explicit resync requests: when a pass runs and what it recovers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from band_sdk_core import RetryTracker

from band.client.streaming import ParticipantRemovedPayload
from band.platform.event import ParticipantRemovedEvent, ReconnectedEvent
from band.runtime.execution import ExecutionContext, ResyncOutcome
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import (
    ROOM_ID,
    ResponseGate,
    rest_client_over,
)
from tests.runtime.resync.kit import ReconciledRoom


class TestResyncPasses:
    """Explicit resync requests: when a pass runs and what it recovers."""

    @pytest.mark.looptime
    @pytest.mark.parametrize("signal", ["request", "reconnect", "play"])
    async def test_explicit_recovery_is_immediate_after_backoff(
        self, reconciled_room: ReconciledRoom, signal: str
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await asyncio.sleep(200)
        peer.add_message("explicit")
        match signal:
            case "request":
                await ctx.request_resync()
            case "reconnect":
                await ctx.on_event(ReconnectedEvent(room_id=ROOM_ID))
            case "play":
                await ctx.resume_room()
        await wait_for_condition(lambda: peer.marked("processed") == ["explicit"])
        assert invoked == ["explicit"]
        assert peer.next_reads[-1] < 201

    @pytest.mark.looptime
    @pytest.mark.parametrize("signal", ["request", "reconnect"])
    async def test_explicit_reconnect_probes_known_stopped_room(
        self, reconciled_room: ReconciledRoom, signal: str
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        ctx.stop_room()
        peer.add_message("played")
        if signal == "request":
            await ctx.request_resync()
        else:
            await ctx.on_event(ReconnectedEvent(room_id=ROOM_ID))
        await wait_for_condition(lambda: peer.marked("processed") == ["played"])
        assert invoked == ["played"]

    @pytest.mark.looptime
    async def test_requests_during_reconciliation_are_not_lost(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, _ = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        gate = ResponseGate(peer, "/next")
        async with rest_client_over(gate.answer) as rest:
            ctx.link.rest = rest
            requested_at = asyncio.get_running_loop().time()
            await ctx.request_resync()
            await gate.entered.wait()
            await ctx.request_resync()
            gate.release.set()
            await wait_for_condition(lambda: len(peer.next_reads) == 3)
        assert peer.next_reads == pytest.approx([0, requested_at, requested_at])

    @pytest.mark.looptime
    async def test_due_pass_coalesces_request_before_ready_events(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        started = asyncio.Event()
        release = asyncio.Event()
        handled: list[str] = []

        async def handler(context: ExecutionContext, event: Any) -> None:
            handled.append(event.payload.id)
            if isinstance(event, ParticipantRemovedEvent):
                started.set()
                await release.wait()
            else:
                invoked.append(event.payload.id)

        ctx._on_execute = handler
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        participant = ParticipantRemovedEvent(
            room_id=ROOM_ID,
            payload=ParticipantRemovedPayload(id="other", name="Other", type="User"),
        )
        await ctx.on_event(participant)
        await started.wait()
        peer.add_message("missed")
        await ctx.on_event(participant)
        await ctx.request_resync()
        await asyncio.sleep(61)
        release.set()
        await asyncio.sleep(1)
        assert len(peer.next_reads) == 3
        assert invoked == ["missed"]
        assert handled == ["other", "missed", "other"]

    @pytest.mark.looptime
    async def test_shutdown_cancels_blocked_probe(
        self, reconciled_room: ReconciledRoom
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        gate = ResponseGate(peer, "/next")
        async with rest_client_over(gate.answer) as rest:
            ctx.link.rest = rest
            await ctx.request_resync()
            await gate.entered.wait()
            await ctx.stop()
            gate.release.set()
            await asyncio.sleep(180)
        assert len(peer.next_reads) == 2
        assert invoked == []

    @pytest.mark.looptime
    @pytest.mark.parametrize("path", ["startup", "resync"])
    async def test_retryable_handler_failure_is_retried_in_the_same_drain(
        self, reconciled_room: ReconciledRoom, path: str
    ) -> None:
        ctx, peer, invoked = reconciled_room
        peer.add_message("retryable")
        ctx._retry_tracker = RetryTracker(max_retries=3)
        ctx.config.report_turn_failures_to_room = False

        async def transient_handler(context: ExecutionContext, event: Any) -> None:
            invoked.append(event.payload.id)
            if len(invoked) == 1:
                raise RuntimeError("transient failure")

        ctx._on_execute = transient_handler
        if path == "startup":
            assert await ctx._synchronize_with_next()
        else:
            assert await ctx._resync_pending_messages() is ResyncOutcome.WORK
        assert invoked == ["retryable", "retryable"]
        assert peer.marked("processed") == ["retryable"]

    @pytest.mark.looptime
    async def test_unchanged_completed_head_defers_without_spinning(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        ctx.claims.remember_completed(ROOM_ID, "stuck")
        peer.add_message("stuck")
        assert await ctx._resync_pending_messages() is ResyncOutcome.BLOCKED
        assert len(peer.next_reads) == 1
        assert peer.actionable_cursors == [None]
        assert peer.requested("processed") == []
        assert invoked == []
