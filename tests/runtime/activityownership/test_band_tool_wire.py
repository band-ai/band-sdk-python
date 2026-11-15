from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools import BandTool
from band.runtime.tools.agent import AgentTools
from tests.activitysupport import IDLE_BODY, REASONING_BODY, tool_body
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import HeldReplyPlatform, deliver_event
from tests.runtime.helpers import IDLE, KEEP_ALIVE

REPLY = "Found you"


@pytest.mark.looptime
class TestBandToolWireActivity:
    """A model's Band tool call reaches the platform as the turn's detail."""

    @pytest.fixture
    async def activity_platform(self) -> AsyncIterator[HeldReplyPlatform]:
        yield HeldReplyPlatform(held_reply=REPLY)

    async def test_a_model_reply_shows_on_the_wire_until_it_is_posted(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: HeldReplyPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            tools = AgentTools.from_context(ctx)
            outcome = await tools.execute_tool_call_structured(
                BandTool.SEND_MESSAGE, {"content": REPLY, "mentions": ["alice"]}
            )
            assert outcome.ok, outcome.error_message

        ctx = execution(handler)
        turn = asyncio.create_task(deliver_event(ctx, make_message_event()))
        await activity_platform.reply_entered.wait()
        shown = await activity_platform.body_at(1)
        activity_platform.release.set()
        await turn

        assert shown == tool_body(BandTool.SEND_MESSAGE)
        assert activity_platform.room_messages == [REPLY]
        assert activity_platform.activity_bodies == [
            REASONING_BODY,
            tool_body(BandTool.SEND_MESSAGE),
            REASONING_BODY,
            IDLE_BODY,
        ]
        assert activity_platform.reports_without_refreshes == [KEEP_ALIVE, IDLE]
