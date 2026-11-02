from __future__ import annotations

from collections.abc import Callable

import pytest

from band.platform.event import PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.activityownership.kit import (
    ActivityPlatform,
    deliver_event,
    past_a_keep_alive,
    report_every_kind,
)
from tests.runtime.helpers import ROOM_ID


@pytest.mark.looptime
class TestExcludedReporting:
    @pytest.mark.parametrize(
        "settings",
        [{"enable_working_state": False}, {"hub_room_id": ROOM_ID}],
        ids=["disabled", "hub"],
    )
    async def test_excluded_reporting_ignores_observations(
        self,
        execution: Callable[..., ExecutionContext],
        activity_platform: ActivityPlatform,
        settings: dict[str, object],
    ) -> None:
        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            await report_every_kind(AgentTools.from_context(ctx))

        ctx = execution(handler, **settings)
        await deliver_event(ctx, make_message_event())
        await past_a_keep_alive()
        assert not activity_platform.activity_bodies
