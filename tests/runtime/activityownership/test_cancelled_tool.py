from __future__ import annotations

import asyncio
from collections.abc import Callable

from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session

from band.core.turn_lifecycle import TurnTask, bind_turn_task
from band.integrations.mcp.engine import (
    EmbeddedResolver,
    EngineSpec,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
)
from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.tools.registry import TOOL_DEFINITIONS
from band.runtime.tools.types import BandTool
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform


class TestCancelledToolActivity:
    async def test_mcp_request_cancellation_during_initial_report_restores_reasoning_for_live_provider(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        ready, release = asyncio.Event(), asyncio.Event()
        captured: list[AgentTools] = []
        workers: list[TurnTask] = []

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            captured.append(tools)
            workers.append(bind_turn_task(tools, asyncio.create_task(release.wait())))
            ready.set()

        ctx = execution(handler)
        await ctx.start()
        await ctx.on_event(make_message_event())
        await ready.wait()
        await ctx._wait_for_idle(timeout=2)
        request_ids: list[int | str] = []

        def resolve(room_id: str) -> AgentTools:
            request_ids.append(server.get_context().request_context.request_id)
            return captured[0]

        definition = TOOL_DEFINITIONS[BandTool.GET_PARTICIPANTS]
        registration = build_tool_registration(
            definition,
            extend_with_chat_id(definition.input_model, None),
            resolver=EmbeddedResolver(get_tools=resolve),
            strip_chat_id=True,
        )
        server = build_engine(
            EngineSpec(name="activity-cancellation", tools=(registration,))
        )
        try:
            async with create_connected_server_and_client_session(server) as session:
                activity_platform.hold_next_activity = True
                request = asyncio.create_task(
                    session.call_tool(
                        BandTool.GET_PARTICIPANTS, {"chat_id": ctx.room_id}
                    )
                )
                try:
                    await activity_platform.activity_entered.wait()
                    await session.send_notification(
                        types.ClientNotification(
                            types.CancelledNotification(
                                params=types.CancelledNotificationParams(
                                    requestId=request_ids[-1]
                                )
                            )
                        )
                    )
                    await activity_platform.activity_cancelled.wait()
                finally:
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
                    activity_platform.activity_release.set()
                mark = len(activity_platform.activity_bodies)
                await asyncio.sleep(ctx.config.working_keep_alive_seconds + 0.1)
                assert not workers[0].task.done()
                assert len(activity_platform.activity_bodies) > mark
                assert activity_platform.activity_bodies[-1] == {
                    "working": True,
                    "activity": {"kind": "reasoning"},
                }
        finally:
            release.set()
            await workers[0].task
