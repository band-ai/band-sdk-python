from __future__ import annotations

import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.platform.activity.kit import ActivityPeer


class TestActivityVersion:
    @pytest.mark.parametrize(
        ("version", "supported"),
        [
            ({"agent_version": "1.24.99"}, False),
            ({"agent_version": "1.25.0"}, True),
            ({"agent_version": "1.26.10"}, True),
            ({"agent_version": "2.0.0"}, True),
            ({"platform_version": "999.0.0"}, False),
            ({"agent_version": "1.25.0-beta"}, False),
            ({"agent_version": "broken"}, False),
            ({"agent_version": "1.25"}, False),
            ({"agent_version": None}, False),
            ([], False),
        ],
    )
    async def test_agent_version_controls_real_request_detail(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
        version: object,
        supported: bool,
    ) -> None:
        activity_peer.version = version
        detail = AgentActivityDetail(kind="tool_call", name="Bash")
        assert await link.report_activity("room", True, activity=detail)
        assert await link.report_activity("other", True, activity=detail)
        assert len(activity_peer.version_requests) == 1
        expected = {"working": True}
        if supported:
            expected["activity"] = {"kind": "tool_call", "name": "Bash"}
        assert activity_peer.reports == [expected, expected]
        assert "authorization" not in activity_peer.version_requests[0].headers

    @pytest.mark.parametrize("kind", ["reasoning", "memory", "permission"])
    async def test_non_tool_detail_omits_name_and_false_omits_detail(
        self, link: BandLink, activity_peer: ActivityPeer, kind: str
    ) -> None:
        detail = AgentActivityDetail(kind=kind)
        assert await link.report_activity("room", True, activity=detail)
        assert await link.report_activity("room", False, activity=detail)
        assert activity_peer.reports == [
            {"working": True, "activity": {"kind": kind}},
            {"working": False},
        ]

    async def test_boolean_callers_do_not_discover(
        self, link: BandLink, activity_peer: ActivityPeer
    ) -> None:
        assert await link.report_activity("room", True)
        assert await link.report_activity("room", False)
        assert not activity_peer.version_requests
        assert activity_peer.reports == [{"working": True}, {"working": False}]
