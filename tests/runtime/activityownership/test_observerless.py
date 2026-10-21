from __future__ import annotations

from band.runtime.working_state import (
    end_tool_activity,
    report_memory_activity,
    report_tool_activity,
    set_permission_wait,
)
from tests.runtime.activityownership.kit import ActivityPlatform


class TestObserverlessProducers:
    async def test_producers_are_silent_without_an_observer(
        self, observerless_tools: object, activity_platform: ActivityPlatform
    ) -> None:
        await report_tool_activity(observerless_tools, "Bash")
        await set_permission_wait(observerless_tools, True)
        await report_memory_activity(observerless_tools)
        await set_permission_wait(observerless_tools, False)
        await end_tool_activity(observerless_tools)
        assert activity_platform.activity_bodies == []
