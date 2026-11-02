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
        (
            "pushed_before_since",
            "pushed_after_since",
            "reports_after_entry",
            "expectation",
        ),
        [
            pytest.param([], [DETAIL], 1, nullcontext(), id="pushed-and-re-reported"),
            pytest.param(
                [], [DETAIL], 0, MISSING_REPORT, id="reported-only-before-entry"
            ),
            pytest.param([], [OTHER_DETAIL], 1, MISSING_PUSH, id="other-tool-pushed"),
            pytest.param([DETAIL], [], 1, MISSING_PUSH, id="pushed-before-since"),
        ],
    )
    async def test_held_detail_needs_a_push_and_a_report_after_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
        raw_peer: tuple[TrackingWebSocketClient, TopicPeer],
        pushed_before_since: list[dict[str, Any]],
        pushed_after_since: list[dict[str, Any]],
        reports_after_entry: int,
        expectation: AbstractContextManager[object],
    ) -> None:
        install_activity_support(monkeypatch, learned=True)
        capture, link, _ = observed_link
        ws, peer = raw_peer
        capture.deadline_s = 1
        detail = AgentActivityDetail(**DETAIL)
        async with capture.rooms(ws, ROOM):
            for activity in pushed_before_since:
                await peer.emit(
                    ROOM,
                    ActivityPush.CHANGED,
                    {"agent_id": AGENT, "activity": activity},
                )
            since = capture.cursor()
            await link.report_activity(ROOM, True, activity=detail)
            for activity in pushed_after_since:
                await peer.emit(
                    ROOM,
                    ActivityPush.CHANGED,
                    {"agent_id": AGENT, "activity": activity},
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
        ("working_before_since", "after_since", "pushes", "expectation"),
        [
            pytest.param(
                [],
                [False],
                [ActivityPush.STOPPED],
                nullcontext(),
                id="idle-and-stopped",
            ),
            pytest.param([], [False], [], MISSING_STOP, id="idle-without-stop"),
            pytest.param(
                [], [], [ActivityPush.STOPPED], MISSING_REPORT, id="stop-alone"
            ),
            pytest.param(
                [False],
                [],
                [ActivityPush.STOPPED],
                MISSING_REPORT,
                id="idle-before-since",
            ),
            pytest.param(
                [],
                [True],
                [ActivityPush.STOPPED],
                MISSING_REPORT,
                id="working-not-idle",
            ),
        ],
    )
    async def test_finished_needs_an_idle_report_and_a_stop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
        raw_peer: tuple[TrackingWebSocketClient, TopicPeer],
        working_before_since: list[bool],
        after_since: list[bool],
        pushes: list[ActivityPush],
        expectation: AbstractContextManager[object],
    ) -> None:
        install_activity_support(monkeypatch, learned=True)
        capture, link, _ = observed_link
        ws, peer = raw_peer
        capture.deadline_s = 1
        async with capture.rooms(ws, ROOM):
            for working in working_before_since:
                await link.report_activity(ROOM, working)
            since = capture.cursor()
            for working in after_since:
                await link.report_activity(ROOM, working)
            for push in pushes:
                await peer.emit(ROOM, push, {"agent_id": AGENT})
            with expectation:
                await capture.finished(ROOM, AGENT, since=since)

    async def test_finished_rejects_a_stop_pushed_before_its_phase(
        self,
        monkeypatch: pytest.MonkeyPatch,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
        raw_peer: tuple[TrackingWebSocketClient, TopicPeer],
    ) -> None:
        install_activity_support(monkeypatch, learned=True)
        capture, link, _ = observed_link
        ws, peer = raw_peer
        capture.deadline_s = 1
        async with capture.rooms(ws, ROOM):
            await peer.emit(ROOM, ActivityPush.STOPPED, {"agent_id": AGENT})
            since = capture.cursor()
            await link.report_activity(ROOM, False)
            with MISSING_STOP:
                await capture.finished(ROOM, AGENT, since=since)
