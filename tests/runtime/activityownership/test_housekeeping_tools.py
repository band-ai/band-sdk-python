from __future__ import annotations

from collections.abc import Callable

from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_participant_added_event
from tests.runtime.activityownership.kit import ActivityPlatform, deliver_event


class TestHousekeepingActivity:
    async def test_band_tools_in_a_housekeeping_turn_never_start_reporting(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            await AgentTools.from_context(ctx).get_participants()

        ctx = execution(handler)
        await deliver_event(ctx, make_participant_added_event())
        await ctx._join_activity_completion()
        assert activity_platform.activity_bodies == []
