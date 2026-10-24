from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.client.streaming import ControlMode
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.activity.kit import ActivityPeer, activity_context
from tests.runtime.helpers import (
    ROOM_ID,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestActivityOwnership:
    @pytest.mark.parametrize("boundary", ["play", "replacement", "shutdown"])
    async def test_stale_activity_refusal_does_not_stop_a_newer_scope(
        self,
        boundary: str,
    ) -> None:
        peer = ActivityPeer()
        peer.stopped = True
        peer.hold = True
        started = asyncio.Event()
        finish = asyncio.Event()
        completed: list[PlatformEvent] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            started.set()
            await finish.wait()
            completed.append(event)

        observer = AsyncMock()
        async with activity_context(peer, handler, observer, enabled=False) as ctx:
            scope = await ctx.begin_delivery()
            async with asyncio.TaskGroup() as tasks:
                report = tasks.create_task(ctx._report_working_state(True))
                await peer.entered.wait()
                match boundary:
                    case "play":
                        await ctx.resume_room()
                    case "replacement":
                        scope = await ctx.begin_delivery()
                    case "shutdown":
                        await ctx.stop()
                        peer.release.set()
                        assert await report
                        observer.assert_not_awaited()
                        return
                event = make_message_event(msg_id="newer")
                assert scope is not None
                scope.originals = (event,)
                turn = tasks.create_task(ctx.execute_delivery(scope, event))
                await started.wait()
                peer.release.set()
                assert await report
                finish.set()
                assert await turn

            observer.assert_not_awaited()
            assert completed == [event]

    @pytest.mark.parametrize("play_after_stop", [False, True])
    async def test_fresh_activity_stop_after_play_cancels_the_same_turn(
        self,
        play_after_stop: bool,
    ) -> None:
        peer = ActivityPeer()
        handler = BlockingHandler(block=1)
        observer = AsyncMock()
        async with (
            activity_context(peer, handler, observer, enabled=False) as ctx,
            asyncio.TaskGroup() as tasks,
        ):
            turn = tasks.create_task(
                attempt_delivery(ctx, make_message_event(msg_id="trigger"))
            )
            await handler.started.wait()
            await ctx.resume_room()
            peer.stopped = True
            assert await ctx._report_working_state(True)
            if play_after_stop:
                await ctx.resume_room()
            await asyncio.sleep(0)
            assert handler.cancelled.is_set()
            await turn
            if play_after_stop:
                observer.assert_not_awaited()
            else:
                observer.assert_awaited_once_with(ROOM_ID, ControlMode.STOP)
            assert peer.marked("processing") == ["trigger"]
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []
