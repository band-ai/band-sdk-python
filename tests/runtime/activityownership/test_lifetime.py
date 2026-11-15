from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from band.client.streaming import ControlMode
from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.helpers import IDLE, KEEP_ALIVE, LifecyclePlatform
from tests.runtime.scheduler.driver import attempt_delivery


class TestProviderLifetime:
    @pytest.mark.looptime
    async def test_detached_worker_keeps_heartbeat_across_busy_delivery(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        release = asyncio.Event()
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            if workers:
                return
            tools = AgentTools.from_context(ctx)
            workers.append(bind_turn_task(tools, asyncio.create_task(release.wait())))

        ctx = execution(handler)
        await attempt_delivery(ctx, make_message_event(msg_id="original"))
        await asyncio.sleep(3.1)
        await attempt_delivery(ctx, make_message_event(msg_id="busy"))
        assert activity_platform.activity_reports == [KEEP_ALIVE, KEEP_ALIVE]
        assert activity_platform.marked("processed") == ["original", "busy"]
        release.set()
        await workers[0].task
        await asyncio.sleep(0)
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports[-1] == IDLE

    async def test_worker_completion_does_not_clear_running_handler(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        finished, release = asyncio.Event(), asyncio.Event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            worker = asyncio.create_task(asyncio.sleep(0))
            bind_turn_task(AgentTools.from_context(ctx), worker)
            await worker
            finished.set()
            await release.wait()

        ctx = execution(handler)
        turn = asyncio.create_task(attempt_delivery(ctx, make_message_event()))
        await finished.wait()
        assert activity_platform.activity_reports == [KEEP_ALIVE]
        release.set()
        await turn
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]

    @pytest.mark.parametrize("mode", [ControlMode.STOP, ControlMode.INTERRUPT])
    async def test_detached_control_cancels_worker_preserving_settled_ack(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
        mode: ControlMode,
    ) -> None:
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            workers.append(
                bind_turn_task(
                    AgentTools.from_context(ctx),
                    asyncio.create_task(asyncio.Event().wait()),
                )
            )

        ctx = execution(handler)
        await attempt_delivery(ctx, make_message_event(msg_id="original"))
        assert ctx.interrupt(kind=mode)
        await asyncio.gather(workers[0].task, return_exceptions=True)
        await ctx._join_activity_completion()
        assert workers[0].task.cancelled()
        assert activity_platform.marked("processed") == ["original"]
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]

    @pytest.mark.looptime
    async def test_detached_heartbeat_stop_preserves_ack(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            if not workers:
                workers.append(
                    bind_turn_task(
                        AgentTools.from_context(ctx),
                        asyncio.create_task(asyncio.Event().wait()),
                    )
                )

        ctx = execution(handler)
        await attempt_delivery(ctx, make_message_event(msg_id="original"))
        await attempt_delivery(ctx, make_message_event(msg_id="busy"))
        activity_platform.stopped = True
        await asyncio.sleep(3.1)
        assert workers[0].task.cancelled()
        assert activity_platform.marked("processed") == ["original", "busy"]
        assert activity_platform.activity_reports[-1] == IDLE

    @pytest.mark.looptime
    async def test_busy_delivery_keeps_original_cap_deadline(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        release = asyncio.Event()
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            if not workers:
                workers.append(
                    bind_turn_task(
                        AgentTools.from_context(ctx),
                        asyncio.create_task(release.wait()),
                    )
                )

        ctx = execution(handler, max_working_state_seconds=4.0)
        await attempt_delivery(ctx, make_message_event(msg_id="original"))
        await asyncio.sleep(3.1)
        await attempt_delivery(ctx, make_message_event(msg_id="busy"))
        await asyncio.sleep(6)
        assert activity_platform.activity_reports == [KEEP_ALIVE, KEEP_ALIVE]
        assert not workers[0].task.done()
        release.set()
        await workers[0].task
        await asyncio.sleep(0)
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports[-1] == IDLE

    async def test_shutdown_clears_reporting_without_cancelling_detached_worker(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        release = asyncio.Event()
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            workers.append(
                bind_turn_task(
                    AgentTools.from_context(ctx), asyncio.create_task(release.wait())
                )
            )

        ctx = execution(handler)
        await attempt_delivery(ctx, make_message_event())
        await ctx.stop()
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]
        assert not workers[0].task.done()
        release.set()
        await workers[0].task
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]

    async def test_new_worker_admitted_after_previous_completion_gets_own_indicator(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        releases = [asyncio.Event(), asyncio.Event()]
        entered, admitted = asyncio.Event(), asyncio.Event()
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            index = len(workers)
            if index:
                entered.set()
                await admitted.wait()
            workers.append(
                bind_turn_task(
                    AgentTools.from_context(ctx),
                    asyncio.create_task(releases[index].wait()),
                )
            )

        ctx = execution(handler)
        await attempt_delivery(ctx, make_message_event(msg_id="original"))
        turn = asyncio.create_task(
            attempt_delivery(ctx, make_message_event(msg_id="new"))
        )
        await entered.wait()
        releases[0].set()
        await workers[0].task
        await asyncio.sleep(0)
        await ctx._join_activity_completion()
        admitted.set()
        await turn
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE, KEEP_ALIVE]
        releases[1].set()
        await workers[1].task
        await asyncio.sleep(0)
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports == [
            KEEP_ALIVE,
            IDLE,
            KEEP_ALIVE,
            IDLE,
        ]
