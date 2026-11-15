from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from band_sdk_core import AgentFailure

from band.client.streaming import DeliveryStatus
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    TURN_FAILURE_PROVIDER,
    TurnResultAlreadyReported,
)
from band.core.types import MessageType
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import (
    ExecutionContext,
    ExecutionHandler,
)
from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig
from band.runtime.tools import AgentTools
from tests.conftest import (
    make_message_event,
)
from tests.platform.settlement.kit import link_over
from tests.runtime.execution.kit import (
    FinalAttemptFailure,
    HydratedTurns,
    backlog_source,
    websocket_source,
)
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    LifecyclePlatform,
    page,
    rest_client_over,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestTurnFailureReport:
    """A final failed turn is reported to the room as an ``error`` event
    instead of failing silently (report_turn_failures_to_room)."""

    @pytest.fixture
    def error_events(
        self, mock_link: MagicMock, mock_rest_client: MagicMock
    ) -> AsyncMock:
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
    async def _failing_handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
        raise RuntimeError("upstream rejected api_key=sk-live-secret")

    @classmethod
    def _context(
        cls,
        mock_link: MagicMock,
        handler: ExecutionHandler | None = None,
        **config: Any,
    ) -> ExecutionContext:
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
        self, mock_link: MagicMock, mock_rest_client: MagicMock, error_events: AsyncMock
    ) -> None:
        """The room sees the generic failure text, never the exception text
        (which can carry secrets) or its class name, and nobody is mentioned."""
        mock_link.rest.agent_api_messages = mock_rest_client.agent_api_messages

        await attempt_delivery(
            self._context(mock_link), self._backlog_message("msg-backlog")
        )

        mock_link.mark_failed.assert_awaited_once()
        request = error_events.call_args.kwargs["event"]
        assert request.message_type == MessageType.ERROR
        assert request.content == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert "sk-live-secret" not in str(request.metadata)
        assert "RuntimeError" not in str(request.metadata)
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_awaited()

    async def test_websocket_final_failure_posts_an_error_event(
        self, mock_link: MagicMock, error_events: AsyncMock
    ) -> None:
        event = make_message_event(
            room_id="room-123", msg_id="msg-ws", sender_id="user-1"
        )

        assert (
            await attempt_delivery(self._context(mock_link), event)
            is TurnOutcome.ADVANCED
        )

        mock_link.mark_failed.assert_awaited_once()
        assert self._reporters(error_events) == [TURN_FAILURE_PROVIDER]

    async def test_report_waits_for_the_final_attempt(
        self, mock_link: MagicMock, error_events: AsyncMock
    ) -> None:
        """A failure the message will be retried from is not final; only the
        last attempt in the retry budget reports, exactly once."""
        handler = FinalAttemptFailure(budget=2)
        ctx = self._context(mock_link, handler, max_message_retries=handler.budget)
        event = make_message_event(
            room_id="room-123", msg_id="msg-retried", sender_id="user-1"
        )

        async with asyncio.TaskGroup() as tasks:
            delivery = tasks.create_task(attempt_delivery(ctx, event))
            try:
                await handler.final_started.wait()
                mock_link.mark_failed.assert_awaited_once()
                error_events.assert_not_awaited()
            finally:
                handler.finish_final.set()
            assert await delivery is TurnOutcome.ADVANCED

        assert mock_link.mark_failed.await_count == 2
        error_events.assert_awaited_once()

        assert await attempt_delivery(ctx, event) is TurnOutcome.ADVANCED
        error_events.assert_awaited_once()

    @pytest.mark.parametrize("sender_type", ["Agent", "System"])
    async def test_non_human_sender_failure_is_reported(
        self, mock_link: MagicMock, error_events: AsyncMock, sender_type: str
    ) -> None:
        """An error event is never delivered as a turn, so an agent's failed
        message is reported too without two failing agents looping."""
        event = make_message_event(
            room_id="room-123",
            msg_id="msg-non-human",
            sender_id="other-1",
            sender_type=sender_type,
        )

        assert (
            await attempt_delivery(self._context(mock_link), event)
            is TurnOutcome.ADVANCED
        )

        error_events.assert_awaited_once()

    async def test_disabled_flag_suppresses_report(
        self, mock_link: MagicMock, error_events: AsyncMock
    ) -> None:
        await attempt_delivery(
            self._context(mock_link, report_turn_failures_to_room=False),
            self._backlog_message("msg-off"),
        )

        mock_link.mark_failed.assert_awaited_once()
        error_events.assert_not_awaited()

    @pytest.mark.looptime
    @pytest.mark.parametrize(
        "source", [websocket_source, backlog_source], ids=["websocket", "backlog"]
    )
    async def test_history_http_outage_preserves_delivery_and_explicit_refresh_recovers(
        self, source: Callable[[LifecyclePlatform, str], MessageEvent | PlatformMessage]
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("msg-hydration")
        history = peer.add_message("msg-history", listed=False)
        peer.context_response = httpx.Response(
            503, json={"error": "history unavailable"}
        )
        handler = HydratedTurns()
        original = source(peer, "msg-hydration")
        async with rest_client_over(peer.answer) as rest:
            ctx = ExecutionContext(
                ROOM_ID,
                link_over(rest),
                handler,
                agent_id=AGENT_ID,
                config=SessionConfig(
                    enable_context_hydration=True,
                    enable_working_state=False,
                    max_message_retries=1,
                ),
            )
            assert await attempt_delivery(ctx, original) is TurnOutcome.ADVANCED
            assert handler.observed == [("msg-hydration", [])]
            assert ctx.context_hydrated
            assert peer.context_reads > 0
            assert peer.requested_marks == [
                ("msg-hydration", DeliveryStatus.PROCESSING),
                ("msg-hydration", DeliveryStatus.PROCESSED),
            ]
            assert peer.posts == []

            peer.context_response = page([history])
            reads_before_refresh = peer.context_reads
            await ctx.prepare_delivery_context(force_refresh=True)
            assert peer.context_reads > reads_before_refresh
            peer.add_message("msg-restored")
            restored = source(peer, "msg-restored")
            assert await attempt_delivery(ctx, restored) is TurnOutcome.ADVANCED

        assert handler.observed == [
            ("msg-hydration", []),
            ("msg-restored", ["msg-history"]),
        ]
        assert peer.requested_marks == [
            ("msg-hydration", DeliveryStatus.PROCESSING),
            ("msg-hydration", DeliveryStatus.PROCESSED),
            ("msg-restored", DeliveryStatus.PROCESSING),
            ("msg-restored", DeliveryStatus.PROCESSED),
        ]
        assert peer.posts == []

    async def test_failure_the_adapter_reported_is_not_repeated(
        self, mock_link: MagicMock, error_events: AsyncMock
    ) -> None:
        """An adapter that posts its own error event and re-raises gets no
        second, runtime report; the next turn's unreported failure still does."""

        async def reporting_handler(
            ctx: ExecutionContext, event: PlatformEvent
        ) -> None:
            assert isinstance(event, MessageEvent)
            assert event.payload is not None
            if event.payload.id == "msg-reported":
                tools = AgentTools.from_context(ctx)
                await tools.send_failure(AgentFailure("codex", "boom"))
            raise RuntimeError("provider down")

        ctx = self._context(mock_link, reporting_handler)

        await attempt_delivery(ctx, self._backlog_message("msg-reported"))
        assert self._reporters(error_events) == ["codex"]

        await attempt_delivery(ctx, self._backlog_message("msg-unreported"))
        assert self._reporters(error_events) == ["codex", TURN_FAILURE_PROVIDER]

    async def test_an_already_reported_claim_without_a_post_is_still_reported(
        self, mock_link: MagicMock, error_events: AsyncMock
    ) -> None:
        """Only a failure post that landed suppresses the runtime's report."""

        async def handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            raise TurnResultAlreadyReported("reported by a nested handler")

        await attempt_delivery(
            self._context(mock_link, handler),
            self._backlog_message("msg-already-reported"),
        )

        mock_link.mark_failed.assert_awaited_once()
        assert self._reporters(error_events) == [TURN_FAILURE_PROVIDER]

    async def test_report_post_failure_is_swallowed(
        self, mock_link: MagicMock, error_events: AsyncMock
    ) -> None:
        """The turn is already marked failed; a failed report must not stop
        processing from advancing."""
        error_events.side_effect = RuntimeError("network down")

        result = await attempt_delivery(
            self._context(mock_link), self._backlog_message("msg-post-fails")
        )

        assert result is TurnOutcome.ADVANCED
        mock_link.mark_failed.assert_awaited_once()
        error_events.assert_awaited_once()
