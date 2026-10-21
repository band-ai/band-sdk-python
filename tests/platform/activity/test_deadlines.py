from __future__ import annotations

import asyncio

import httpx
import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.activitysupport import BOOLEAN_BODY, DETAIL_BODY
from tests.platform.activity.kit import ActivityPeer

DEADLINE_SECONDS = 2
# Under the deadline alone, but the deadline cannot cover it plus a full resend.
REJECTION_LATENCY_SECONDS = 1.5


@pytest.fixture
def stalled_resend(activity_peer: ActivityPeer) -> ActivityPeer:
    """The detail POST is slowly rejected and its detail-free resend never answers."""
    activity_peer.post_latency[1] = REJECTION_LATENCY_SECONDS
    activity_peer.responses.append(httpx.Response(422))
    activity_peer.stall_post_number = 2
    return activity_peer


@pytest.mark.looptime
@pytest.mark.usefixtures("learned")
class TestActivityDeadline:
    async def test_resend_cannot_extend_the_report_deadline(
        self, link: BandLink, stalled_resend: ActivityPeer, detail: AgentActivityDetail
    ) -> None:
        started = asyncio.get_running_loop().time()
        assert not await link.report_activity(
            "room", True, activity=detail, timeout_seconds=DEADLINE_SECONDS
        )
        assert asyncio.get_running_loop().time() - started == DEADLINE_SECONDS
        assert stalled_resend.reports == [
            DETAIL_BODY,
            BOOLEAN_BODY,
        ]

    async def test_downgrade_survives_a_timed_out_resend(
        self, link: BandLink, stalled_resend: ActivityPeer, detail: AgentActivityDetail
    ) -> None:
        await link.report_activity(
            "room", True, activity=detail, timeout_seconds=DEADLINE_SECONDS
        )
        stalled_resend.stall_post_number = None
        assert await link.report_activity("next", True, activity=detail)
        assert stalled_resend.reports[-1] == BOOLEAN_BODY
