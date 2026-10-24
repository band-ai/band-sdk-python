from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.toolactivity import report_tool_activity
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import (
    BOOLEAN_BODY,
    IDLE_BODY,
    REASONING_BODY,
    install_activity_support,
    tool_body,
)
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event


@pytest.mark.looptime
class TestTurnNegotiation:
    async def test_a_fresh_process_learns_detail_from_its_first_report(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        install_activity_support(monkeypatch, learned=False)

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            await activity_platform.shown(report_tool_activity(tools, "Bash"))

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event())
        assert activity_platform.activity_bodies == [
            BOOLEAN_BODY,
            tool_body("Bash"),
            IDLE_BODY,
        ]

    async def test_a_rejected_detail_mid_turn_leaves_every_later_report_boolean(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            if event.payload.id == "second":
                return
            tools = AgentTools.from_context(ctx)
            activity_platform.activity_responses.append(httpx.Response(422))
            await activity_platform.shown(report_tool_activity(tools, "Bash"))
            # The resend, then one keep-alive.
            await activity_platform.bodies_reach(4)

        ctx = execution(handler)
        await deliver_event(ctx, make_message_event(msg_id="first"))
        await deliver_event(ctx, make_message_event(msg_id="second"))
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body("Bash"),
            BOOLEAN_BODY,
            BOOLEAN_BODY,
            IDLE_BODY,
            BOOLEAN_BODY,
            IDLE_BODY,
        ]
