from __future__ import annotations

import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.e2e.baseline.toolkit.control import ControlLink, ControlRuntime
from tests.platform.activity.kit import DETAIL, ActivityPeer


@pytest.mark.usefixtures("learned")
class TestControlLinkActivity:
    async def test_baseline_control_link_forwards_activity_detail(
        self, link: BandLink, activity_peer: ActivityPeer, detail: AgentActivityDetail
    ) -> None:
        control_link = ControlLink(agent_id="agent", api_key="test-key")
        control_link.rest = link.rest
        control_link.control = ControlRuntime()
        control_link.control_room_id = "room"
        assert await control_link.report_activity("room", True, activity=detail)
        assert activity_peer.reports == [{"working": True, "activity": DETAIL}]
