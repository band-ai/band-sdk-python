from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.platform.activity.kit import ActivityPeer


class TestActivityFallback:
    @pytest.mark.parametrize(
        "body", [b'{"errors":{"activity":["unknown"]}}', b"not json", b""]
    )
    async def test_422_retries_without_detail_and_latches_across_links(
        self, link: BandLink, activity_peer: ActivityPeer, body: bytes
    ) -> None:
        activity_peer.responses.append(httpx.Response(422, content=body))
        detail = AgentActivityDetail(kind="tool_call", name="Read")
        assert await link.report_activity("first", True, activity=detail)
        other = BandLink(
            agent_id="other-agent", api_key="test-key", rest_url="https://other.test"
        )
        other.rest = link.rest
        assert await other.report_activity("second", True, activity=detail)
        assert activity_peer.reports == [
            {"working": True, "activity": {"kind": "tool_call", "name": "Read"}},
            {"working": True},
            {"working": True},
        ]
        assert len(activity_peer.version_requests) == 1

    @pytest.mark.parametrize("status", [400, 403, 500])
    async def test_other_errors_do_not_retry_or_disable(
        self, link: BandLink, activity_peer: ActivityPeer, status: int
    ) -> None:
        activity_peer.responses.append(
            httpx.Response(status, json={"error": "failure"})
        )
        detail = AgentActivityDetail(kind="reasoning")
        assert not await link.report_activity("room", True, activity=detail)
        assert await link.report_activity("room", True, activity=detail)
        assert (
            activity_peer.reports
            == [{"working": True, "activity": {"kind": "reasoning"}}] * 2
        )

    async def test_fallback_echoed_false_keeps_stop_notification(
        self, link: BandLink, activity_peer: ActivityPeer
    ) -> None:
        activity_peer.responses.extend(
            [
                httpx.Response(422),
                httpx.Response(200, json={"data": {"working": False}}),
            ]
        )
        stopped = Mock()
        assert await link.report_activity(
            "room",
            True,
            activity=AgentActivityDetail(kind="reasoning"),
            on_stopped=stopped,
        )
        stopped.assert_called_once_with()
        assert activity_peer.reports[-1] == {"working": True}
