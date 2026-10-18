from __future__ import annotations

import asyncio

import pytest
from band_rest import AgentActivityDetail

from band.platform.activity import ActivitySupport
from band.platform.link import BandLink
from tests.platform.activity.kit import VERSION_URL, ActivityPeer


class TestActivityCoordination:
    async def test_concurrent_links_share_discovery_and_waiter_cancellation_is_independent(
        self, link: BandLink, peer: ActivityPeer
    ) -> None:
        peer.discovery_release = asyncio.Event()
        detail = AgentActivityDetail(kind="reasoning")
        owner = asyncio.create_task(
            link.report_activity("first", True, activity=detail)
        )
        await peer.discovering.wait()
        other = BandLink(
            agent_id="second", api_key="test", rest_url="https://platform.test"
        )
        other.rest = link.rest
        waiter = asyncio.create_task(
            other.report_activity("second", True, activity=detail)
        )
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not owner.done()
        peer.discovery_release.set()
        assert await owner
        assert await other.report_activity("second", True, activity=detail)
        assert len(peer.version_requests) == 1
        assert (
            peer.reports == [{"working": True, "activity": {"kind": "reasoning"}}] * 2
        )

    async def test_discovery_owner_cancellation_unblocks_future_callers_as_boolean(
        self, link: BandLink, peer: ActivityPeer
    ) -> None:
        peer.discovery_release = asyncio.Event()
        detail = AgentActivityDetail(kind="reasoning")
        owner = asyncio.create_task(
            link.report_activity("first", True, activity=detail)
        )
        await peer.discovering.wait()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert await link.report_activity("next", True, activity=detail)
        assert peer.reports == [{"working": True}]
        assert len(peer.version_requests) == 1

    def test_completed_negotiation_survives_later_event_loops(
        self, support: ActivitySupport, peer: ActivityPeer
    ) -> None:
        assert asyncio.run(support.allows(VERSION_URL.removesuffix("/api/version"), 1))
        assert asyncio.run(support.allows("https://unqueried.test", 1))
        assert len(peer.version_requests) == 1

    @pytest.mark.parametrize("failure", ["server", "malformed"])
    async def test_discovery_failure_is_cached_without_harming_boolean_reports(
        self, link: BandLink, peer: ActivityPeer, failure: str
    ) -> None:
        if failure == "server":
            peer.version_status = 500
        else:
            peer.version_content = b"not-json"
        for room in ("first", "second"):
            assert await link.report_activity(
                room, True, activity=AgentActivityDetail(kind="memory")
            )
        assert len(peer.version_requests) == 1
        assert peer.reports == [{"working": True}] * 2
