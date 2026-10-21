from __future__ import annotations

from collections.abc import Callable

from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import (
    HeldWorkerHandler,
    IdleReportGate,
    consume_control,
)
from tests.runtime.helpers import IDLE, KEEP_ALIVE, LifecyclePlatform


class TestControlProbe:
    async def test_probe_leaves_message_owners_indicator_alone(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
    ) -> None:
        handler = HeldWorkerHandler()
        ctx = execution(handler, on_message_control=consume_control)
        await ctx._process_event(make_message_event(msg_id="owner"))

        assert await ctx._try_control(make_message_event(msg_id="approve"))
        await ctx._join_activity_completion()
        assert activity_platform.marked("processed") == ["owner", "approve"]
        assert not handler.workers[0].task.done()
        assert activity_platform.activity_reports == [KEEP_ALIVE]

        await handler.finish(ctx)
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]

    async def test_probe_is_not_held_behind_owners_idle_report(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: LifecyclePlatform,
        idle_report_gate: IdleReportGate,
    ) -> None:
        handler = HeldWorkerHandler()
        ctx = execution(handler, on_message_control=consume_control)
        await ctx._process_event(make_message_event(msg_id="owner"))
        idle_report_gate.release.clear()
        handler.release.set()
        await idle_report_gate.entered.wait()

        assert await ctx._try_control(make_message_event(msg_id="approve"))
        assert activity_platform.marked("processed") == ["owner", "approve"]

        idle_report_gate.release.set()
        await ctx._join_activity_completion()
        assert activity_platform.activity_reports == [KEEP_ALIVE, IDLE]
