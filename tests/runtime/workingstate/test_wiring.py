from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.event import ReconnectedEvent
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event, make_participant_added_event
from tests.runtime.scheduler.driver import attempt_delivery
from tests.runtime.workingstate.kit import (
    _FAST,
    _backlog_message,
    _independent_link,
    _working_values,
)


class TestWiring:
    @pytest.mark.asyncio
    async def test_message_event_reports_true_then_false(
        self, mock_link: MagicMock, mock_handler: AsyncMock
    ) -> None:
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await attempt_delivery(ctx, make_message_event(room_id="room-123", msg_id="m1"))

        assert _working_values(mock_link) == [True, False]
        for call in mock_link.report_activity.call_args_list:
            assert call.args[0] == "room-123"

    @pytest.mark.asyncio
    async def test_reports_false_even_when_handler_raises(
        self, mock_link: MagicMock
    ) -> None:
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        ctx = ExecutionContext("room-123", mock_link, handler, config=_FAST)

        await attempt_delivery(ctx, make_message_event(room_id="room-123", msg_id="m1"))

        assert _working_values(mock_link)[-1] is False
        assert _working_values(mock_link).count(False) == 1

    @pytest.mark.asyncio
    async def test_participant_event_does_not_report(
        self, mock_link: MagicMock, mock_handler: AsyncMock
    ) -> None:
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await attempt_delivery(
            ctx,
            make_participant_added_event(room_id="room-123", participant_id="user-2"),
        )

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnected_event_does_not_report(
        self, mock_link: MagicMock, mock_handler: AsyncMock
    ) -> None:
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await attempt_delivery(ctx, ReconnectedEvent(room_id="room-123"))

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_hub_room_does_not_report(
        self, mock_link: MagicMock, mock_handler: AsyncMock
    ) -> None:
        ctx = ExecutionContext(
            "hub-1", mock_link, mock_handler, config=_FAST, hub_room_id="hub-1"
        )

        await attempt_delivery(ctx, make_message_event(room_id="hub-1", msg_id="m1"))

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_backlog_message_reports_true_then_false(
        self, mock_link: MagicMock, mock_handler: AsyncMock
    ) -> None:
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await attempt_delivery(ctx, _backlog_message())

        assert _working_values(mock_link) == [True, False]

    @pytest.mark.asyncio
    async def test_disabled_config_does_not_report(
        self, mock_link: MagicMock, mock_handler: AsyncMock
    ) -> None:
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_working_state=False),
        )

        await attempt_delivery(ctx, make_message_event(room_id="room-123", msg_id="m1"))

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_rooms_are_isolated(self, mock_handler: AsyncMock) -> None:
        link_a = _independent_link()
        link_b = _independent_link()
        ctx_a = ExecutionContext("room-a", link_a, mock_handler, config=_FAST)
        ExecutionContext("room-b", link_b, mock_handler, config=_FAST)

        await attempt_delivery(ctx_a, make_message_event(room_id="room-a", msg_id="m1"))

        link_a.report_activity.assert_called()
        link_b.report_activity.assert_not_called()
