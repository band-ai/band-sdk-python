from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.client.streaming import ControlMode
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.activity.kit import ActivityPeer, activity_context
from tests.runtime.helpers import (
    IDLE,
    KEEP_ALIVE,
    STOP_ECHO,
    ActivityReport,
    running_execution,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestActivityCleanup:
    async def test_repeated_activity_refusal_does_not_cancel_handler_cleanup(
        self,
    ) -> None:
        peer = ActivityPeer()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        finish = asyncio.Event()
        cleaned_up = asyncio.Event()
        observer = AsyncMock()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            started.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                cancelled.set()
                await finish.wait()
                cleaned_up.set()

        async with activity_context(peer, handler, observer, enabled=False) as ctx:
            async with asyncio.TaskGroup() as tasks:
                turn = tasks.create_task(
                    attempt_delivery(ctx, make_message_event(msg_id="trigger"))
                )
                await started.wait()
                peer.stopped = True
                await ctx._report_working_state(True)
                await cancelled.wait()
                await ctx._report_working_state(True)
                finish.set()
                await turn
                await cleaned_up.wait()

            assert cleaned_up.is_set()
            observer.assert_awaited_once()
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []

    @pytest.mark.looptime
    async def test_activity_refusal_preserves_native_stop_cleanup(
        self,
    ) -> None:
        peer = ActivityPeer()
        started = asyncio.Event()
        reports_at_cleanup: list[ActivityReport] = []
        cleaning, release = asyncio.Event(), asyncio.Event()
        observer = AsyncMock()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            started.set()
            try:
                await asyncio.Future[None]()
            finally:
                cleaning.set()
                await release.wait()
                reports_at_cleanup.extend(peer.activity_reports)

        async with activity_context(peer, handler, observer) as ctx:
            async with asyncio.TaskGroup() as tasks:
                turn = tasks.create_task(
                    attempt_delivery(ctx, make_message_event(msg_id="trigger"))
                )
                await started.wait()
                peer.stopped = True
                ctx.stop_room()
                await cleaning.wait()
                await ctx._report_working_state(True)
                release.set()
                await turn

            # The final idle report follows the cleanup it reports on.
            assert reports_at_cleanup == [KEEP_ALIVE, STOP_ECHO]
            assert peer.activity_reports == [*reports_at_cleanup, IDLE]
            observer.assert_not_awaited()
            assert peer.marked("processing") == ["trigger"]
            assert peer.requested("processed") == []
            assert peer.requested("failed") == []

    @pytest.mark.parametrize("refusal", ["post", "claim"])
    async def test_stopped_tool_preserves_awaited_cleanup(self, refusal: str) -> None:
        peer = ActivityPeer()
        cleanup: list[str] = []
        cleaning, release = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            peer.stopped = True
            try:
                if refusal == "claim":
                    await ctx.claim_message("auxiliary")
                else:
                    await AgentTools.from_context(ctx).send_message(
                        "reply", mentions=[{"id": "user-1", "type": "user"}]
                    )
            finally:
                cleaning.set()
                await release.wait()
                cleanup.append("completed")

        async with activity_context(peer, handler, AsyncMock(), enabled=False) as ctx:
            turn = asyncio.create_task(
                attempt_delivery(ctx, make_message_event(msg_id="trigger"))
            )
            await cleaning.wait()
            release.set()
            await turn
        assert cleanup == ["completed"]
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []

    @pytest.mark.parametrize(
        ("first", "second", "processed"),
        [
            (ControlMode.STOP, ControlMode.STOP, []),
            (ControlMode.STOP, ControlMode.INTERRUPT, ["trigger"]),
            (ControlMode.INTERRUPT, ControlMode.STOP, []),
        ],
    )
    async def test_started_room_controls_preserve_cleanup_and_latest_disposition(
        self, first: ControlMode, second: ControlMode, processed: list[str]
    ) -> None:
        peer = ActivityPeer()
        started, cleaning, release, finished = (asyncio.Event() for _ in range(4))
        cleanup: list[str] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            started.set()
            try:
                await asyncio.Future[None]()
            finally:
                cleaning.set()
                await release.wait()
                cleanup.append("completed")
                finished.set()

        async with activity_context(peer, handler, AsyncMock()) as ctx:
            async with running_execution(ctx):
                await ctx.on_event(make_message_event(msg_id="trigger"))
                await started.wait()
                ctx.interrupt(kind=first)
                await cleaning.wait()
                ctx.interrupt(kind=second)
                release.set()
                await finished.wait()
                await ctx._idle_event.wait()
            assert cleanup == ["completed"]
            assert peer.requested("processed") == processed
            assert peer.requested("failed") == []
            assert peer.activity_reports == [KEEP_ALIVE, IDLE]
