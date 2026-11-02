"""Report records preserve real response outcomes and dispatch-phase identity."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from band_rest import AgentActivityDetail

from tests.e2e.baseline.toolkit.activity import (
    ActivityCapture,
    ObservingBandLink,
    ReportOutcome,
)
from tests.framework_conformance.activitycapture.kit import HeldActivityPeer


class TestReportCapture:
    @pytest.mark.looptime
    async def test_http_failure_is_sent_but_not_an_accepted_report_or_stop(
        self,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
    ) -> None:
        capture, link, peer = observed_link
        capture.deadline_s = 1
        peer.responses.append(httpx.Response(503, json={"error": "Unavailable"}))
        stops: list[str] = []

        assert not await link.report_activity(
            "room", True, on_stopped=lambda: stops.append("original")
        )
        assert await capture.reports[0].outcome == ReportOutcome(
            accepted=False, stopped=False
        )
        assert stops == []
        assert await capture.sent("room", working=True) is capture.reports[0]
        with pytest.raises(TimeoutError, match="report room="):
            await capture.report("room", working=True)

    async def test_a_failed_request_waits_for_the_next_accepted_keep_alive(
        self,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
    ) -> None:
        capture, link, peer = observed_link
        peer.responses.append(httpx.Response(503, json={"error": "Unavailable"}))
        assert not await link.report_activity("room", True)
        assert await link.report_activity("room", True)
        record = await capture.report("room", working=True)
        assert record is capture.reports[1]

    async def test_original_stop_callback_failure_reaches_the_report_outcome(
        self,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
    ) -> None:
        capture, link, peer = observed_link
        peer.responses.append(httpx.Response(200, json={"data": {"working": False}}))
        failure = ValueError("Original control callback failed")

        def fail_control() -> None:
            raise failure

        with pytest.raises(ValueError) as sent:
            await link.report_activity("room", True, on_stopped=fail_control)
        with pytest.raises(ValueError) as captured:
            await capture.report("room", working=True)
        assert sent.value is captured.value is failure

    async def test_cancelled_http_request_cannot_complete_a_success_barrier(
        self,
        observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer],
    ) -> None:
        capture, link, peer = observed_link
        peer.hold = True
        sending = asyncio.create_task(link.report_activity("room", True))
        try:
            await peer.entered.wait()
            sending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sending
            with pytest.raises(asyncio.CancelledError):
                await capture.report("room", working=True)
            assert sending.cancelled()
        finally:
            peer.release.set()
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)

    async def test_successful_http_stop_is_observed_and_forwarded(
        self, observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer]
    ) -> None:
        capture, link, peer = observed_link
        stops: list[str] = []
        peer.responses.append(httpx.Response(200, json={"data": {"working": False}}))
        assert await link.report_activity(
            "room",
            True,
            activity=AgentActivityDetail(kind="tool_call", name="Read"),
            on_stopped=lambda: stops.append("original"),
        )
        record = capture.reports[0]
        assert record.room_id == "room"
        assert record.activity == {"kind": "tool_call", "name": "Read"}
        assert await record.outcome == ReportOutcome(accepted=True, stopped=True)
        assert stops == ["original"]

    async def test_cursor_requires_a_request_dispatched_in_the_new_phase(
        self, observed_link: tuple[ActivityCapture, ObservingBandLink, HeldActivityPeer]
    ) -> None:
        capture, link, peer = observed_link
        peer.hold = True
        old = asyncio.create_task(link.report_activity("room", True))
        try:
            await peer.entered.wait()
            phase = capture.cursor()
            peer.release.set()
            assert await old
            assert capture.reports[0].sequence <= phase
            await link.report_activity("room", True)
            record = await capture.report("room", working=True, since=phase)
            assert record is capture.reports[1]
        finally:
            peer.release.set()
            await old
