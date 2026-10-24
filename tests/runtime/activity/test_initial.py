from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.activity.kit import ActivityPeer, activity_context
from tests.runtime.helpers import (
    IDLE,
    KEEP_ALIVE,
    STOP_ECHO,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestActivityInitial:
    async def test_stop_on_initial_activity_report_never_enters_handler(
        self,
    ) -> None:
        peer = ActivityPeer()
        peer.stopped = True
        handler = BlockingHandler(block=False)
        observer = AsyncMock()
        async with activity_context(peer, handler, observer) as ctx:
            scope = await ctx.begin_delivery()
            assert scope is not None
            event = make_message_event(msg_id="trigger")
            scope.originals = (event,)
            assert not await ctx.execute_delivery(scope, event)

            assert handler.invoked == []
            assert peer.activity_reports == [STOP_ECHO, IDLE]
            observer.assert_awaited_once()
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []

    async def test_shutdown_during_initial_activity_report_cleans_up_reporter(
        self,
    ) -> None:
        peer = ActivityPeer()
        peer.hold = True
        handler = BlockingHandler(block=False)
        observer = AsyncMock()
        async with activity_context(peer, handler, observer) as ctx:
            async with asyncio.TaskGroup() as tasks:
                turn = tasks.create_task(
                    attempt_delivery(ctx, make_message_event(msg_id="trigger"))
                )
                await peer.entered.wait()
                turn.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await turn
            assert handler.invoked == []
            assert peer.activity_reports == [KEEP_ALIVE, IDLE]
            observer.assert_not_awaited()
