from __future__ import annotations

import logging

import pytest
from band_rest import AgentActivityDetail

from band.platform.link import BandLink
from tests.activitysupport import SUPPORTED_VERSION
from tests.platform.activity.kit import DETAIL, ActivityPeer


class TestActivityVersion:
    @pytest.mark.parametrize(
        ("version", "supported"),
        [
            ("1.24.99", False),
            pytest.param(SUPPORTED_VERSION, True, id="minimum-version-on-saas-default"),
            ("1.26.10", True),
            ("2.0.0", True),
            ("1.25.0-beta", False),
            ("broken", False),
            ("1.25", False),
            ("2.0", False),
            (None, False),
        ],
    )
    async def test_advertised_version_controls_later_detail(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
        detail: AgentActivityDetail,
        version: str | None,
        supported: bool,
    ) -> None:
        activity_peer.version = version
        assert await link.report_activity("room", True, activity=detail)
        assert await link.report_activity("room", True, activity=detail)
        later = (
            {"working": True, "activity": DETAIL} if supported else {"working": True}
        )
        assert activity_peer.reports == [{"working": True}, later]
        assert all(
            request.url.path.endswith("/activity") for request in activity_peer.requests
        )

    async def test_learned_support_is_shared_across_links(
        self,
        link: BandLink,
        second_link: BandLink,
        activity_peer: ActivityPeer,
        detail: AgentActivityDetail,
    ) -> None:
        assert await link.report_activity("first", True)
        assert await second_link.report_activity("second", True, activity=detail)
        assert activity_peer.reports[-1] == {"working": True, "activity": DETAIL}

    @pytest.mark.parametrize("kind", ["reasoning", "memory", "permission"])
    async def test_non_tool_detail_omits_name_and_false_omits_detail(
        self, link: BandLink, activity_peer: ActivityPeer, kind: str
    ) -> None:
        detail = AgentActivityDetail(kind=kind)
        assert await link.report_activity("room", True)
        assert await link.report_activity("room", True, activity=detail)
        assert await link.report_activity("room", False, activity=detail)
        assert activity_peer.reports[1:] == [
            {"working": True, "activity": {"kind": kind}},
            {"working": False},
        ]

    async def test_unadvertised_version_is_logged_for_diagnosis(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        activity_peer.version = "1.24.0"
        with caplog.at_level(logging.DEBUG, logger="band.platform.activity"):
            assert await link.report_activity("room", True)
        [record] = caplog.records
        assert record.levelno == logging.DEBUG
        assert "'1.24.0'" in record.getMessage()
