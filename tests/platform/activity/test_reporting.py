from __future__ import annotations

import logging

import httpx
import pytest

from band.platform.link import BandLink
from tests.platform.activity.kit import ActivityPeer


class TestActivityReporting:
    @pytest.mark.parametrize("working", [True, False])
    async def test_reports_the_boolean_state_to_the_room(
        self, link: BandLink, activity_peer: ActivityPeer, working: bool
    ) -> None:
        assert await link.report_activity("room-1", working)
        assert activity_peer.reports == [{"working": working}]
        assert activity_peer.requests[-1].url.path.endswith("/chats/room-1/activity")

    async def test_post_is_bounded_and_never_retried(
        self, link: BandLink, activity_peer: ActivityPeer
    ) -> None:
        activity_peer.responses.append(httpx.Response(500))
        assert not await link.report_activity("room-1", True, timeout_seconds=2)
        [request] = activity_peer.requests
        assert request.extensions["timeout"]["read"] == 2

    async def test_repeated_failures_warn_once_then_recover(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        activity_peer.responses.extend([httpx.Response(500), httpx.Response(500)])
        with caplog.at_level(logging.INFO, logger="band.platform.message_lifecycle"):
            assert not await link.report_activity("room-1", True)
            assert not await link.report_activity("room-1", True)
            assert await link.report_activity("room-1", True)
        assert [record.levelno for record in caplog.records] == [
            logging.WARNING,
            logging.INFO,
        ]
        assert "recovered" in caplog.records[-1].getMessage()
