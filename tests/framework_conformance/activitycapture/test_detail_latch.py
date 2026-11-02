"""A detail barrier fails loudly once this process latched activity detail off."""

from __future__ import annotations

import pytest
from band_rest import AgentActivityDetail

from tests.activitysupport import install_activity_support
from tests.e2e.baseline.toolkit.activity import (
    DETAIL_DISABLED,
    ActivityCapture,
    ObservingBandLink,
)
from tests.framework_conformance.activitycapture.kit import HeldActivityPeer

ROOM = "room"
DETAIL = {"kind": "tool_call", "name": "Read"}


class TestDetailLatch:
    async def test_detail_report_is_matched_while_detail_is_supported(
        self,
        monkeypatch: pytest.MonkeyPatch,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
    ) -> None:
        install_activity_support(monkeypatch, learned=True)
        capture, link, peer = observed_link
        await link.report_activity(ROOM, True, activity=AgentActivityDetail(**DETAIL))
        record = await capture.report(ROOM, working=True, activity=DETAIL)
        assert (record.activity, peer.reports) == (
            DETAIL,
            [{"working": True, "activity": DETAIL}],
        )

    async def test_latched_detail_cannot_pass_as_an_accepted_detail_report(
        self,
        monkeypatch: pytest.MonkeyPatch,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
    ) -> None:
        support = install_activity_support(monkeypatch, learned=True)
        support.disable(room_id=ROOM)
        capture, link, peer = observed_link
        await link.report_activity(ROOM, True, activity=AgentActivityDetail(**DETAIL))
        with pytest.raises(AssertionError, match=DETAIL_DISABLED):
            await capture.report(ROOM, working=True, activity=DETAIL)
        assert peer.reports == [{"working": True}]

    async def test_boolean_report_is_still_matched_once_detail_is_latched_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
    ) -> None:
        support = install_activity_support(monkeypatch, learned=True)
        support.disable(room_id=ROOM)
        capture, link, _ = observed_link
        await link.report_activity(ROOM, True)
        record = await capture.report(ROOM, working=True)
        assert (record.working, record.activity) == (True, None)
