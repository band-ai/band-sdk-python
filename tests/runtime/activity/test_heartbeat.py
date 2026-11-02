from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.platform.event import PlatformEvent
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.activity.kit import ActivityPeer, activity_context
from tests.runtime.helpers import (
    IDLE,
    KEEP_ALIVE,
    STOP_ECHO,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestActivityHeartbeat:
    @pytest.mark.looptime
    async def test_watchdog_revokes_reporting_before_provider_cleanup(
        self,
    ) -> None:
        peer = ActivityPeer()
        observer = AsyncMock()
        started, cleaning, release = (asyncio.Event() for _ in range(3))

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        async with activity_context(
            peer, handler, observer, max_cycle_seconds=2.5
        ) as ctx:
            turn = asyncio.create_task(
                attempt_delivery(ctx, make_message_event(msg_id="trigger"))
            )
            await started.wait()
            await cleaning.wait()
            peer.stopped = True
            assert not await ctx._report_working_state(True)
            release.set()
            assert await turn is TurnOutcome.RETRY_LATER
            assert peer.activity_reports == [KEEP_ALIVE, IDLE]
            assert peer.marked("processing") == ["trigger"]
            assert peer.requested("failed") == ["trigger"]
            observer.assert_not_awaited()

    @pytest.mark.looptime
    async def test_heartbeat_stop_cancels_once_and_preserves_replay(
        self,
    ) -> None:
        peer = ActivityPeer()
        handler = BlockingHandler(block=1)
        observer = AsyncMock()
        async with activity_context(peer, handler, observer) as ctx:
            event = make_message_event(msg_id="trigger")
            async with asyncio.TaskGroup() as tasks:
                turn = tasks.create_task(attempt_delivery(ctx, event))
                await handler.started.wait()
                peer.stopped = True
                await turn

            assert handler.cancelled.is_set()
            assert handler.completed == []
            assert peer.activity_reports == [KEEP_ALIVE, STOP_ECHO, IDLE]
            assert peer.marked("processing") == ["trigger"]
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []
            observer.assert_not_awaited()

            peer.stopped = False
            await ctx.resume_room()
            await attempt_delivery(ctx, event)
            assert handler.invoked == ["trigger", "trigger"]
            assert handler.completed == ["trigger"]
            assert peer.marked("processed") == ["trigger"]
            observer.assert_not_awaited()
