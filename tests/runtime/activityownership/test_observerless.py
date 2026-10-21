from __future__ import annotations

import pytest

from band.runtime.tools.agent import AgentTools
from band.testing import FakeAgentTools
from tests.runtime.activityownership.kit import (
    ActivityPlatform,
    past_a_keep_alive,
    report_every_kind,
)


@pytest.mark.looptime
class TestObserverlessProducers:
    async def test_tools_outside_an_execution_report_nothing(
        self, bare_tools: AgentTools, activity_platform: ActivityPlatform
    ) -> None:
        fake = FakeAgentTools()
        await report_every_kind(fake)
        await report_every_kind(bare_tools)
        await past_a_keep_alive()
        assert activity_platform.activity_bodies == []
        assert fake.tool_calls == []
