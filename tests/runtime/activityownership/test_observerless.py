from __future__ import annotations

from band.runtime.tools.agent import AgentTools
from band.testing import FakeAgentTools
from tests.runtime.activityownership.kit import ActivityPlatform, report_every_kind


class TestObserverlessProducers:
    async def test_producers_accept_tools_that_carry_no_observer(self) -> None:
        await report_every_kind(FakeAgentTools())

    async def test_tools_outside_an_execution_report_nothing(
        self, bare_tools: AgentTools, activity_platform: ActivityPlatform
    ) -> None:
        await report_every_kind(bare_tools)
        assert activity_platform.activity_bodies == []
