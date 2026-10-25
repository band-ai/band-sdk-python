"""Names-only activity reaches the wire independently of tool narration."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from band.core.types import Emit, HistoryProvider
from band.platform.event import PlatformEvent
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event
from tests.runtime.activityownership.narrationkit import (
    EXPECTED_ACTIVITY_BODIES,
    ActivityToolAdapter,
    turn_input,
)
from tests.runtime.helpers import AGENT_ID


@pytest.mark.looptime
@pytest.mark.parametrize("emit", [(), (Emit.TOOL_CALLS,)])
class TestActivityIndependentOfNarration:
    async def test_direct_turn_reports_named_activity(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        emit: tuple[Emit, ...],
    ) -> None:
        adapter = ActivityToolAdapter(activity_platform, emit=emit)
        message = make_message_event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            payload = DefaultPreprocessor.message(ctx, event, AGENT_ID)
            assert payload is not None
            await adapter.run_judged_turn(
                msg=payload,
                tools=AgentTools.from_context(ctx),
                history=HistoryProvider(raw=[]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id=ctx.room_id,
            )

        ctx = execution(handler)
        await deliver_event(ctx, message)
        assert activity_platform.activity_bodies == EXPECTED_ACTIVITY_BODIES

    async def test_batch_reports_named_activity(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        emit: tuple[Emit, ...],
    ) -> None:
        adapter = ActivityToolAdapter(activity_platform, emit=emit)
        first = make_message_event(msg_id="first")
        second = make_message_event(msg_id="second")

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            first_payload = DefaultPreprocessor.message(ctx, event, AGENT_ID)
            second_payload = DefaultPreprocessor.message(ctx, second, AGENT_ID)
            assert first_payload is not None
            assert second_payload is not None
            await adapter.on_messages(turn_input(ctx, (first_payload, second_payload)))

        ctx = execution(handler)
        await deliver_event(ctx, first)
        assert activity_platform.activity_bodies == EXPECTED_ACTIVITY_BODIES

    async def test_event_reports_named_activity(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        emit: tuple[Emit, ...],
    ) -> None:
        adapter = ActivityToolAdapter(activity_platform, emit=emit)
        message = make_message_event()

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            payload = DefaultPreprocessor.message(ctx, event, AGENT_ID)
            assert payload is not None
            await adapter.on_event(turn_input(ctx, (payload,)))

        ctx = execution(handler)
        await deliver_event(ctx, message)
        assert activity_platform.activity_bodies == EXPECTED_ACTIVITY_BODIES
