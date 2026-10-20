from __future__ import annotations

import asyncio

import httpx
import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.platform.activity.kit import DETAIL, ActivityPeer


@pytest.mark.looptime
@pytest.mark.usefixtures("learned")
class TestActivityDeadline:
    async def test_retry_cannot_extend_total_deadline_and_downgrade_survives_timeout(
        self, link: BandLink, activity_peer: ActivityPeer, detail: AgentActivityDetail
    ) -> None:
        activity_peer.responses.append(httpx.Response(422))
        activity_peer.stall_post_number = 2
        started = asyncio.get_running_loop().time()
        assert not await link.report_activity(
            "room", True, activity=detail, timeout_seconds=2
        )
        assert asyncio.get_running_loop().time() - started <= 2.01
        assert activity_peer.reports == [
            {"working": True, "activity": DETAIL},
            {"working": True},
        ]
        activity_peer.stall_post_number = None
        assert await link.report_activity("next", True, activity=detail)
        assert activity_peer.reports[-1] == {"working": True}
