from __future__ import annotations

import logging
from unittest.mock import Mock

import httpx
import pytest
from band_rest import AgentActivityDetail

from band.platform.activity import ActivitySupport
from band.platform.link import BandLink
from tests.platform.activity.kit import DETAIL, ActivityPeer


@pytest.mark.usefixtures("learned")
class TestActivityFallback:
    @pytest.mark.parametrize(
        "body", [b'{"errors":{"activity":["unknown"]}}', b"not json", b""]
    )
    async def test_422_retries_without_detail_and_latches_across_links(
        self,
        link: BandLink,
        second_link: BandLink,
        activity_peer: ActivityPeer,
        detail: AgentActivityDetail,
        body: bytes,
    ) -> None:
        activity_peer.responses.append(httpx.Response(422, content=body))
        assert await link.report_activity("first", True, activity=detail)
        assert await second_link.report_activity("second", True, activity=detail)
        assert activity_peer.reports == [
            {"working": True, "activity": DETAIL},
            {"working": True},
            {"working": True},
        ]

    @pytest.mark.parametrize(
        ("working", "with_detail"),
        [(True, False), (False, True)],
        ids=["no-detail", "idle"],
    )
    async def test_422_without_detail_neither_retries_nor_latches(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
        learned: ActivitySupport,
        detail: AgentActivityDetail,
        working: bool,
        with_detail: bool,
    ) -> None:
        activity_peer.responses.append(httpx.Response(422))
        activity = detail if with_detail else None
        assert not await link.report_activity("room", working, activity=activity)
        assert len(activity_peer.reports) == 1
        assert learned.supported

    async def test_downgrade_logs_the_rejected_field_not_the_body(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
        detail: AgentActivityDetail,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        activity_peer.responses.append(
            httpx.Response(
                422,
                json={
                    "error": {
                        "code": "validation_error",
                        "details": {"/activity/name": ["echoed Bash --secret"]},
                        "message": "Invalid request",
                        "request_id": "request-1",
                    }
                },
            )
        )
        with caplog.at_level(logging.DEBUG, logger="band.platform"):
            assert await link.report_activity("room", True, activity=detail)
        [record] = [r for r in caplog.records if r.levelno == logging.INFO]
        assert "/activity/name" in record.getMessage()
        assert "secret" not in record.getMessage()
        assert any(
            r.levelno == logging.DEBUG and "secret" in r.getMessage()
            for r in caplog.records
        )

    @pytest.mark.parametrize("status", [400, 403, 500])
    async def test_other_errors_do_not_retry_or_disable(
        self,
        link: BandLink,
        activity_peer: ActivityPeer,
        detail: AgentActivityDetail,
        status: int,
    ) -> None:
        activity_peer.responses.append(
            httpx.Response(status, json={"error": "failure"})
        )
        assert not await link.report_activity("room", True, activity=detail)
        assert await link.report_activity("room", True, activity=detail)
        assert activity_peer.reports == [{"working": True, "activity": DETAIL}] * 2

    async def test_fallback_echoed_false_keeps_stop_notification(
        self, link: BandLink, activity_peer: ActivityPeer, detail: AgentActivityDetail
    ) -> None:
        activity_peer.responses.extend(
            [
                httpx.Response(422),
                httpx.Response(200, json={"data": {"working": False}}),
            ]
        )
        stopped = Mock()
        assert await link.report_activity(
            "room", True, activity=detail, on_stopped=stopped
        )
        stopped.assert_called_once_with()
        assert activity_peer.reports[-1] == {"working": True}
