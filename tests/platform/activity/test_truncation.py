"""I-08: a tool name too long for the platform is truncated, never rejected."""

from __future__ import annotations

import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.platform.activity.kit import ActivityPeer

LIMIT = 1024


@pytest.mark.usefixtures("learned")
class TestNameTruncation:
    async def test_over_long_name_is_clipped_before_the_post(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
    ) -> None:
        long_name = "a" * (LIMIT + 16)
        sent = await link.report_activity(
            "room",
            True,
            activity=AgentActivityDetail(kind="tool_call", name=long_name),
        )
        assert sent
        assert activity_peer.reports == [
            {
                "working": True,
                "activity": {"kind": "tool_call", "name": "a" * LIMIT},
            }
        ]

    async def test_exact_limit_name_is_untouched(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
    ) -> None:
        detail = AgentActivityDetail(kind="tool_call", name="b" * LIMIT)
        assert await link.report_activity("room", True, activity=detail)
        assert activity_peer.reports[0]["activity"]["name"] == "b" * LIMIT
