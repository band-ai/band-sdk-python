from __future__ import annotations

import asyncio

import httpx
import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.platform.activity.kit import ActivityPeer


@pytest.mark.looptime
class TestActivityDeadline:
    async def test_stalled_discovery_reserves_half_the_budget_for_boolean_post(
        self, link: BandLink, peer: ActivityPeer
    ) -> None:
        peer.discovery_release = asyncio.Event()
        started = asyncio.get_running_loop().time()
        assert await link.report_activity(
            "room", True, activity=AgentActivityDetail(kind="reasoning")
        )
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed <= 1.01
        assert peer.reports == [{"working": True}]
        assert len(peer.version_requests) == 1

    async def test_retry_cannot_extend_total_deadline_and_downgrade_survives_timeout(
        self, link: BandLink, peer: ActivityPeer
    ) -> None:
        peer.responses.append(httpx.Response(422))
        peer.hold_post_number = 2
        detail = AgentActivityDetail(kind="tool_call", name="Bash")
        started = asyncio.get_running_loop().time()
        assert not await link.report_activity("room", True, activity=detail)
        elapsed = asyncio.get_running_loop().time() - started
        assert elapsed <= 2.01
        assert peer.reports == [
            {"working": True, "activity": {"kind": "tool_call", "name": "Bash"}},
            {"working": True},
        ]
        peer.hold_post_number = None
        assert await link.report_activity("next", True, activity=detail)
        assert peer.reports[-1] == {"working": True}
