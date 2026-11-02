"""Held and finished barriers need both a raw push and a report from their phase."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest
from band_rest import AgentActivityDetail

from tests.activitysupport import install_activity_support
from tests.e2e.baseline.toolkit.activity import (
    ActivityCapture,
    ActivityPush,
    ObservingBandLink,
)
from tests.e2e.baseline.toolkit.ws import TrackingWebSocketClient
from tests.framework_conformance.activitycapture.kit import HeldActivityPeer
from tests.framework_conformance.activitycapture.topics import TopicPeer

pytestmark = pytest.mark.looptime

ROOM = "room"
AGENT = "agent"
DETAIL = {"kind": "tool_call", "name": "Read"}
OTHER_DETAIL = {"kind": "tool_call", "name": "Write"}
MISSING_PUSH = pytest.raises(TimeoutError, match="raw detail")
MISSING_REPORT = pytest.raises(TimeoutError, match="report dispatched")
MISSING_STOP = pytest.raises(TimeoutError, match="raw stop")


class TestActivityBarriers:
    @pytest.mark.parametrize(
        ("pushed", "reports_after_entry", "expectation"),
        [
            pytest.param(DETAIL, 1, nullcontext(), id="pushed-and-re-reported"),
            pytest.param(DETAIL, 0, MISSING_REPORT, id="reported-only-before-entry"),
            pytest.param(OTHER_DETAIL, 1, MISSING_PUSH, id="other-tool-pushed"),
        ],
    )
    async def test_held_detail_needs_a_push_and_a_report_after_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
        raw_peer: tuple[TrackingWebSocketClient, TopicPeer],
        pushed: dict[str, Any],
        reports_after_entry: int,
        expectation: AbstractContextManager[object],
    ) -> None:
        install_activity_support(monkeypatch, learned=True)
        capture, link, _ = observed_link
        ws, peer = raw_peer
        capture.deadline_s = 1
        detail = AgentActivityDetail(**DETAIL)
        async with capture.rooms(ws, ROOM):
            since = capture.cursor()
            await link.report_activity(ROOM, True, activity=detail)
            await peer.emit(
                ROOM, ActivityPush.CHANGED, {"agent_id": AGENT, "activity": pushed}
            )
            entered = capture.cursor()
            for _ in range(reports_after_entry):
                await link.report_activity(ROOM, True, activity=detail)
            with expectation:
                record = await capture.held(
                    ROOM, AGENT, DETAIL, since=since, entered=entered
                )
                assert record.sequence > entered

    @pytest.mark.parametrize(
        ("idle_reports", "pushes", "expectation"),
        [
            pytest.param(
                1, [ActivityPush.STOPPED], nullcontext(), id="idle-and-stopped"
            ),
            pytest.param(1, [], MISSING_STOP, id="idle-without-stop"),
            pytest.param(0, [ActivityPush.STOPPED], MISSING_REPORT, id="stop-alone"),
        ],
    )
    async def test_finished_needs_an_idle_report_and_a_stop(
        self,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
        raw_peer: tuple[TrackingWebSocketClient, TopicPeer],
        idle_reports: int,
        pushes: list[ActivityPush],
        expectation: AbstractContextManager[object],
    ) -> None:
        capture, link, _ = observed_link
        ws, peer = raw_peer
        capture.deadline_s = 1
        async with capture.rooms(ws, ROOM):
            since = capture.cursor()
            for _ in range(idle_reports):
                await link.report_activity(ROOM, False)
            for push in pushes:
                await peer.emit(ROOM, push, {"agent_id": AGENT})
            with expectation:
                await capture.finished(ROOM, AGENT, since=since)
