from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from band.platform.event import PlatformEvent
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
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
    async def test_heartbeat_stop_during_watchdog_cleanup_keeps_stop_disposition(
        self,
    ) -> None:
        peer = ActivityPeer()
        observer = AsyncMock()
        keep_alive = SessionConfig().working_keep_alive_seconds

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                # The platform stops the room while the watchdog's cleanup runs
                # through the next keep-alive.
                peer.stopped = True
                await asyncio.sleep(CYCLE_CANCEL_GRACE_SECONDS - 0.1)
                raise

        async with activity_context(
            peer, handler, observer, max_cycle_seconds=keep_alive - 0.5
        ) as ctx:
            assert (
                await attempt_delivery(ctx, make_message_event(msg_id="trigger"))
                is TurnOutcome.RETRY_LATER
            )

            assert peer.activity_reports == [KEEP_ALIVE, STOP_ECHO, IDLE]
            assert peer.marked("processing") == ["trigger"]
            assert peer.requested("failed") == []
            observer.assert_awaited_once()

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
            observer.assert_awaited_once()

            peer.stopped = False
            await ctx.resume_room()
            await attempt_delivery(ctx, event)
            assert handler.invoked == ["trigger", "trigger"]
            assert handler.completed == ["trigger"]
            assert peer.marked("processed") == ["trigger"]
            observer.assert_awaited_once()
