from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from band_sdk_core import AgentFailure

from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TURN_FAILURE_PROVIDER,
    TurnResultAlreadyReported,
)
from band.core.types import MessageType
from band.runtime.execution import (
    ExecutionContext,
    TurnOutcome,
)
from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools import AgentTools
from tests.conftest import (
    make_message_event,
)


class TestTurnFailureReport:
    """A final failed turn is reported to the room as an ``error`` event
    instead of failing silently (report_turn_failures_to_room)."""

    @pytest.fixture
    def error_events(self, mock_link, mock_rest_client) -> AsyncMock:
        """The real-signature event-create mock ``send_failure`` posts through."""
        mock_link.rest.agent_api_events = mock_rest_client.agent_api_events
        return mock_rest_client.agent_api_events.create_agent_chat_event

    @staticmethod
    def _reporters(error_events: AsyncMock) -> list[str]:
        """The ``AgentFailure.provider`` of each error event posted, in order."""
        return [
            call.kwargs["event"].metadata["failure"]["provider"]
            for call in error_events.call_args_list
        ]

    @staticmethod
    async def _failing_handler(ctx, event) -> None:
        raise RuntimeError("upstream rejected api_key=sk-live-secret")

    @classmethod
    def _context(cls, mock_link, handler=None, **config) -> ExecutionContext:
        return ExecutionContext(
            "room-123",
            mock_link,
            handler or cls._failing_handler,
            config=SessionConfig(enable_context_hydration=False, **config),
        )

    @staticmethod
    def _backlog_message(msg_id: str) -> PlatformMessage:
        return PlatformMessage(
            id=msg_id,
            room_id="room-123",
            content="Test",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

    async def test_backlog_final_failure_posts_a_generic_error_event(
        self, mock_link, mock_rest_client, error_events
    ):
        """The room sees the generic failure text, never the exception text
        (which can carry secrets) or its class name, and nobody is mentioned."""
        mock_link.rest.agent_api_messages = mock_rest_client.agent_api_messages

        await self._context(mock_link)._process_backlog_message(
            self._backlog_message("msg-backlog")
        )

        mock_link.mark_failed.assert_awaited_once()
        request = error_events.call_args.kwargs["event"]
        assert request.message_type == MessageType.ERROR
        assert request.content == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert "sk-live-secret" not in str(request.metadata)
        assert "RuntimeError" not in str(request.metadata)
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_awaited()

    async def test_websocket_final_failure_posts_an_error_event(
        self, mock_link, error_events
    ):
        event = make_message_event(
            room_id="room-123", msg_id="msg-ws", sender_id="user-1"
        )

        assert (
            await self._context(mock_link)._process_event(event) is TurnOutcome.ADVANCED
        )

        mock_link.mark_failed.assert_awaited_once()
        assert self._reporters(error_events) == [TURN_FAILURE_PROVIDER]

    async def test_report_waits_for_the_final_attempt(self, mock_link, error_events):
        """A failure the message will be retried from is not final; only the
        last attempt in the retry budget reports, exactly once."""
        ctx = self._context(mock_link, max_message_retries=2)
        event = make_message_event(
            room_id="room-123", msg_id="msg-retried", sender_id="user-1"
        )

        assert await ctx._process_event(event) is TurnOutcome.ADVANCED
        mock_link.mark_failed.assert_awaited_once()
        error_events.assert_not_awaited()

        assert await ctx._process_event(event) is TurnOutcome.ADVANCED
        assert mock_link.mark_failed.await_count == 2
        error_events.assert_awaited_once()

        assert await ctx._process_event(event) is TurnOutcome.ADVANCED
        error_events.assert_awaited_once()

    @pytest.mark.parametrize("sender_type", ["Agent", "System"])
    async def test_non_human_sender_failure_is_reported(
        self, mock_link, error_events, sender_type
    ):
        """An error event is never delivered as a turn, so an agent's failed
        message is reported too without two failing agents looping."""
        event = make_message_event(
            room_id="room-123",
            msg_id="msg-non-human",
            sender_id="other-1",
            sender_type=sender_type,
        )

        assert (
            await self._context(mock_link)._process_event(event) is TurnOutcome.ADVANCED
        )

        error_events.assert_awaited_once()

    async def test_disabled_flag_suppresses_report(self, mock_link, error_events):
        await self._context(
            mock_link, report_turn_failures_to_room=False
        )._process_backlog_message(self._backlog_message("msg-off"))

        mock_link.mark_failed.assert_awaited_once()
        error_events.assert_not_awaited()

    @pytest.mark.parametrize("path", ["websocket", "backlog"])
    async def test_hydration_failure_is_reported_on_either_path(
        self, mock_link, error_events, path
    ):
        """A history outage is charged to the retry budget before hydration on
        both delivery paths, so it is reported the same whichever carried it."""
        ctx = self._context(mock_link)
        ctx._ensure_fresh_context = AsyncMock(side_effect=RuntimeError("history down"))

        if path == "websocket":
            await ctx._process_event(
                make_message_event(
                    room_id="room-123", msg_id="msg-hydration", sender_id="user-1"
                )
            )
        else:
            await ctx._process_backlog_message(self._backlog_message("msg-hydration"))

        mock_link.mark_failed.assert_awaited_once()
        error_events.assert_awaited_once()

    async def test_failure_the_adapter_reported_is_not_repeated(
        self, mock_link, error_events
    ):
        """An adapter that posts its own error event and re-raises gets no
        second, runtime report; the next turn's unreported failure still does."""

        async def reporting_handler(ctx, event):
            if event.payload.id == "msg-reported":
                tools = AgentTools.from_context(ctx)
                await tools.send_failure(AgentFailure("codex", "boom"))
            raise RuntimeError("provider down")

        ctx = self._context(mock_link, reporting_handler)

        await ctx._process_backlog_message(self._backlog_message("msg-reported"))
        assert self._reporters(error_events) == ["codex"]

        await ctx._process_backlog_message(self._backlog_message("msg-unreported"))
        assert self._reporters(error_events) == ["codex", TURN_FAILURE_PROVIDER]

    async def test_an_already_reported_claim_without_a_post_is_still_reported(
        self, mock_link, error_events
    ):
        """Only a failure post that landed suppresses the runtime's report."""

        async def handler(ctx, event):
            raise TurnResultAlreadyReported("reported by a nested handler")

        await self._context(mock_link, handler)._process_backlog_message(
            self._backlog_message("msg-already-reported")
        )

        mock_link.mark_failed.assert_awaited_once()
        assert self._reporters(error_events) == [TURN_FAILURE_PROVIDER]

    async def test_report_post_failure_is_swallowed(self, mock_link, error_events):
        """The turn is already marked failed; a failed report must not stop
        processing from advancing."""
        error_events.side_effect = RuntimeError("network down")

        result = await self._context(mock_link)._process_backlog_message(
            self._backlog_message("msg-post-fails")
        )

        assert result is TurnOutcome.ADVANCED
        mock_link.mark_failed.assert_awaited_once()
        error_events.assert_awaited_once()
