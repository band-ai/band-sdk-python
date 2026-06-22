"""Integration tests: ExecutionContext wiring of the working-state reporter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.event import ReconnectedEvent
from band.runtime.execution import ExecutionContext
from band.runtime.types import PlatformMessage, SessionConfig
from tests.conftest import (
    make_message_event,
    make_participant_added_event,
)


@pytest.fixture
def mock_link():
    link = MagicMock()
    link.agent_id = "agent-123"
    link.rest = MagicMock()

    participant1 = MagicMock()
    participant1.id = "user-1"
    participant1.name = "User One"
    participant1.type = "User"
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[participant1])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )

    link.mark_processing = AsyncMock(return_value=True)
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.report_activity = AsyncMock(return_value=True)
    return link


def _working_values(link) -> list[bool]:
    """Sequence of `working` booleans passed to link.report_activity."""
    return [
        c.args[1] if len(c.args) > 1 else c.kwargs["working"]
        for c in link.report_activity.call_args_list
    ]


# Keep cadence high enough that no keep-alive fires during a fast test cycle,
# but below the TTL/2 guard.
_FAST = SessionConfig(working_keep_alive_seconds=4.0)


def _backlog_message(
    room_id: str = "room-123", msg_id: str = "msg-b1"
) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=room_id,
        content="hi",
        sender_id="user-1",
        sender_type="User",
        sender_name="User One",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


class TestWiring:
    @pytest.mark.asyncio
    async def test_message_event_reports_true_then_false(self, mock_link, mock_handler):
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await ctx._process_event(make_message_event(room_id="room-123", msg_id="m1"))

        assert _working_values(mock_link) == [True, False]
        for call in mock_link.report_activity.call_args_list:
            assert call.args[0] == "room-123"

    @pytest.mark.asyncio
    async def test_reports_false_even_when_handler_raises(self, mock_link):
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        ctx = ExecutionContext("room-123", mock_link, handler, config=_FAST)

        await ctx._process_event(make_message_event(room_id="room-123", msg_id="m1"))

        assert _working_values(mock_link)[-1] is False
        assert _working_values(mock_link).count(False) == 1

    @pytest.mark.asyncio
    async def test_participant_event_does_not_report(self, mock_link, mock_handler):
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await ctx._process_event(
            make_participant_added_event(room_id="room-123", participant_id="user-2")
        )

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnected_event_does_not_report(self, mock_link, mock_handler):
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await ctx._process_event(ReconnectedEvent(room_id="room-123"))

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_hub_room_does_not_report(self, mock_link, mock_handler):
        ctx = ExecutionContext(
            "hub-1", mock_link, mock_handler, config=_FAST, hub_room_id="hub-1"
        )

        await ctx._process_event(make_message_event(room_id="hub-1", msg_id="m1"))

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_backlog_message_reports_true_then_false(
        self, mock_link, mock_handler
    ):
        ctx = ExecutionContext("room-123", mock_link, mock_handler, config=_FAST)

        await ctx._process_backlog_message(_backlog_message())

        assert _working_values(mock_link) == [True, False]

    @pytest.mark.asyncio
    async def test_disabled_config_does_not_report(self, mock_link, mock_handler):
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_working_state=False),
        )

        await ctx._process_event(make_message_event(room_id="room-123", msg_id="m1"))

        mock_link.report_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_rooms_are_isolated(self, mock_handler):
        link_a = _independent_link()
        link_b = _independent_link()
        ctx_a = ExecutionContext("room-a", link_a, mock_handler, config=_FAST)
        ExecutionContext("room-b", link_b, mock_handler, config=_FAST)

        await ctx_a._process_event(make_message_event(room_id="room-a", msg_id="m1"))

        link_a.report_activity.assert_called()
        link_b.report_activity.assert_not_called()


def _independent_link():
    link = MagicMock()
    link.agent_id = "agent-123"
    link.rest = MagicMock()
    p = MagicMock(id="user-1", name="User One", type="User")
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[p])
    )
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.mark_processing = AsyncMock(return_value=True)
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    link.report_activity = AsyncMock(return_value=True)
    return link


@pytest.fixture
def mock_handler():
    return AsyncMock()


class TestSessionConfigGuards:
    def test_defaults_are_valid(self):
        cfg = SessionConfig()
        assert cfg.enable_working_state is True
        assert cfg.working_keep_alive_seconds == 3.0
        assert cfg.working_request_timeout_seconds == 2
        assert cfg.max_working_state_seconds is None

    def test_cadence_must_be_below_ttl_half(self):
        with pytest.raises(ValueError):
            SessionConfig(working_keep_alive_seconds=5.0)  # >= 10/2

    def test_cadence_must_be_positive(self):
        with pytest.raises(ValueError):
            SessionConfig(working_keep_alive_seconds=0)

    def test_timeout_must_be_below_cadence(self):
        with pytest.raises(ValueError):
            SessionConfig(
                working_keep_alive_seconds=3.0, working_request_timeout_seconds=3
            )

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValueError):
            SessionConfig(working_request_timeout_seconds=0)

    def test_max_duration_if_set_must_be_positive(self):
        with pytest.raises(ValueError):
            SessionConfig(max_working_state_seconds=0)

    def test_disabled_skips_guards(self):
        # When disabled, odd values must not raise.
        cfg = SessionConfig(
            enable_working_state=False, working_keep_alive_seconds=100.0
        )
        assert cfg.enable_working_state is False
