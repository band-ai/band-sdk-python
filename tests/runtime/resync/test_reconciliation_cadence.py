"""Periodic /next reconciliation: its interval, growth and what resets it."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
import pytest

from band.client.streaming import ParticipantRemovedPayload
from band.platform.event import ParticipantRemovedEvent
from band.runtime.execution import ExecutionContext
from band.runtime.resync import ResyncSchedule
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import (
    ROOM_ID,
    rest_client_over,
)
from tests.runtime.resync.kit import ReconciledRoom


class TestReconciliationCadence:
    """Periodic /next reconciliation: its interval, growth and what resets it."""

    @pytest.mark.looptime
    async def test_traffic_cannot_postpone_missed_message_recovery(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        peer.add_message("missed")
        for _ in range(3):
            await asyncio.sleep(20)
            await ctx.on_event(
                ParticipantRemovedEvent(
                    room_id=ROOM_ID,
                    payload=ParticipantRemovedPayload(
                        id="unrelated", name="Other", type="User"
                    ),
                )
            )
        await asyncio.sleep(1)
        assert invoked == ["missed"]
        assert peer.marked("processed") == ["missed"]

    @pytest.mark.looptime
    async def test_quiet_reconciliation_grows_to_conservative_cap(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await asyncio.sleep(301)
        assert peer.next_reads == pytest.approx([0, 60, 180, 300])
        assert invoked == []

    @pytest.mark.looptime
    async def test_new_work_brings_backed_off_deadline_forward(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await asyncio.sleep(200)
        peer.add_message("live")
        work_at = asyncio.get_running_loop().time()
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="live"))
        await wait_for_condition(lambda: peer.marked("processed") == ["live"])
        await asyncio.sleep(61)
        assert invoked == ["live"]
        assert peer.next_reads[-1] == pytest.approx(work_at + 60)

    @pytest.mark.looptime
    async def test_duplicate_delivery_does_not_reset_quiet_growth(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await asyncio.sleep(200)
        ctx.claims.remember_completed(ROOM_ID, "duplicate")
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="duplicate"))
        await asyncio.sleep(61)
        assert peer.next_reads == pytest.approx([0, 60, 180])
        assert invoked == []

    @pytest.mark.looptime
    async def test_stop_shortens_an_already_armed_quiet_wait(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await asyncio.sleep(200)
        stopped_at = asyncio.get_running_loop().time()
        ctx.stop_room()
        peer.stopped = True
        await asyncio.sleep(61)
        assert peer.next_reads[-1] == pytest.approx(stopped_at + 60)
        assert invoked == []

    @pytest.mark.looptime
    async def test_deadline_does_not_cancel_handler_or_issue_catchup_burst(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(context: ExecutionContext, event: Any) -> None:
            started.set()
            await release.wait()
            invoked.append(event.payload.id)

        ctx._on_execute = handler
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        peer.add_message("slow")
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="slow"))
        await started.wait()
        await asyncio.sleep(301)
        assert invoked == []
        assert len(peer.next_reads) == 1
        release.set()
        await asyncio.sleep(1)
        assert invoked == ["slow"]
        assert peer.marked("processed") == ["slow"]
        assert len(peer.next_reads) == 2

    @pytest.mark.looptime
    async def test_custom_base_above_default_maximum_keeps_its_interval(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, _ = reconciled_room
        ctx.config = SessionConfig(idle_resync_seconds=300, enable_working_state=False)
        ctx._resync_schedule = ResyncSchedule(300, 120)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await asyncio.sleep(601)
        assert peer.next_reads == pytest.approx([0, 300, 600])

    @pytest.mark.looptime
    async def test_initial_spread_does_not_shorten_recurring_intervals(
        self, reconciled_room: ReconciledRoom, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx, peer, _ = reconciled_room
        monkeypatch.setattr(random, "uniform", lambda lower, upper: upper / 2)
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await asyncio.sleep(271)
        assert peer.next_reads == pytest.approx([0, 30, 150, 270])

    @pytest.mark.looptime
    async def test_http_error_retains_base_retry_cadence(
        self,
        reconciled_room: ReconciledRoom,
    ) -> None:
        ctx, peer, invoked = reconciled_room
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        failure_pending = True

        def answer(request: httpx.Request) -> httpx.Response:
            nonlocal failure_pending
            response = peer.answer(request)
            if request.url.path.endswith("/next") and failure_pending:
                failure_pending = False
                return httpx.Response(400)
            return response

        async with rest_client_over(answer) as rest:
            ctx.link.rest = rest
            await asyncio.sleep(241)
        assert peer.next_reads == pytest.approx([0, 60, 120, 240])
        assert invoked == []
