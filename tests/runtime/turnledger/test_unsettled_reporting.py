"""Unsettled turns report missing replies once."""

from __future__ import annotations

import logging

import pytest

from band.core.turn import Turn, report_unsettled_turn
from band.testing.fake_tools import (
    MISSING_REPLY_FAILURE,
    FakeAgentTools,
    failure_reports,
    reported_failures,
)


class TestReportUnsettledTurn:
    async def test_a_missing_reply_is_reported_once(self) -> None:
        tools = FakeAgentTools()

        assert await report_unsettled_turn(tools, room_id="room-1") is True
        assert await report_unsettled_turn(tools, room_id="room-1") is False

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]

    async def test_a_missing_reply_is_logged_for_its_room(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="band.core.turn"):
            await report_unsettled_turn(FakeAgentTools(), room_id="room-1")

        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "room-1" in record.getMessage()

    async def test_a_session_that_reports_no_failures_posts_nothing(self) -> None:
        """A detached turn reports outside the runtime, so the opt-out is the
        turn's own to honor."""
        tools = FakeAgentTools()
        tools.turn = Turn(posts_missing_reply=False)

        assert await report_unsettled_turn(tools, room_id="room-1") is True

        assert reported_failures(tools) == []

    async def test_a_complete_turn_reports_nothing(self) -> None:
        tools = FakeAgentTools()
        await tools.no_reply()

        assert await report_unsettled_turn(tools, room_id="room-1") is False

        assert reported_failures(tools) == []

    async def test_a_settled_turn_reports_nothing(self) -> None:
        tools = FakeAgentTools()
        tools.turn.settle()

        assert await report_unsettled_turn(tools, room_id="room-1") is False
