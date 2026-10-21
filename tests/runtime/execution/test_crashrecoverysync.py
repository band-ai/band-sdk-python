from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_sdk_core import ClaimRegistry

from band.client.streaming import MessageMetadata
from band.platform.event import ParticipantAddedEvent, PlatformEvent
from band.runtime.execution import (
    ExecutionContext,
    ResyncOutcome,
    TurnOutcome,
)
from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_message_event,
    make_participant_added_event,
)
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import running_execution


class TestCrashRecoverySync:
    """Test crash recovery sync mechanism."""

    @pytest.fixture
    def mock_link_with_next(self):
        """Mock BandLink with message lifecycle methods."""
        link = MagicMock()
        link.agent_id = "agent-123"
        link.rest = MagicMock()

        # Default: no messages
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # Message lifecycle methods (new in BandLink)
        link.mark_processing = AsyncMock()
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        link.get_next_message = AsyncMock(return_value=None)  # No backlog by default
        link.get_stale_processing_messages = AsyncMock(return_value=[])  # No stale msgs
        link.get_actionable_messages = AsyncMock(return_value=[])
        link.report_activity = AsyncMock(return_value=True)

        return link

    async def test_first_ws_message_sets_marker(
        self, mock_link_with_next, mock_handler
    ):
        """First WebSocket message should set sync point marker."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        assert ctx._first_ws_msg_id is None

        event = make_message_event(room_id="room-123", msg_id="msg-ws-001")
        await ctx.on_event(event)

        assert ctx._first_ws_msg_id == "msg-ws-001"

    async def test_subsequent_ws_messages_dont_change_marker(
        self, mock_link_with_next, mock_handler
    ):
        """Subsequent WebSocket messages should not change the marker."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        event1 = make_message_event(room_id="room-123", msg_id="msg-ws-001")
        event2 = make_message_event(room_id="room-123", msg_id="msg-ws-002")

        await ctx.on_event(event1)
        await ctx.on_event(event2)

        assert ctx._first_ws_msg_id == "msg-ws-001"

    async def test_sync_completes_with_no_backlog(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should complete immediately when no backlog."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        assert ctx._sync_complete is True
        mock_link_with_next.get_next_message.assert_called()

        await ctx.stop()

    async def test_sync_processes_backlog_messages(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should process backlog messages from /next."""

        # Setup get_next_message to return one backlog message, then None
        backlog_msg = PlatformMessage(
            id="msg-backlog-001",
            room_id="room-123",
            content="Backlog message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        mock_link_with_next.get_next_message = AsyncMock(
            side_effect=[backlog_msg, None]
        )

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.start()
        await wait_for_condition(lambda: mock_handler.call_count >= 1)

        # Handler should be called for backlog message
        assert mock_handler.call_count >= 1
        # The first call should be for backlog message
        call_args = mock_handler.call_args_list[0][0]
        assert call_args[1].payload.id == "msg-backlog-001"

        await ctx.stop()

    async def test_sync_point_clears_marker_and_keeps_dedupe_cache(
        self, mock_link_with_next, mock_handler
    ):
        """When sync point is reached, marker is cleared and dedupe is preserved."""

        # Setup: WS message arrives, then /next returns same message
        sync_msg = PlatformMessage(
            id="msg-sync-001",
            room_id="room-123",
            content="Sync message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        mock_link_with_next.get_next_message = AsyncMock(return_value=sync_msg)

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Enqueue WS message first (sets marker)
        ws_event = make_message_event(room_id="room-123", msg_id="msg-sync-001")
        await ctx.on_event(ws_event)
        assert ctx._first_ws_msg_id == "msg-sync-001"

        # Start should sync and find sync point
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        # Marker should be cleared
        assert ctx._first_ws_msg_id is None
        # Dedupe cache should keep processed sync id to avoid WS reprocessing
        assert "msg-sync-001" in ctx.claims.completed_ids(ctx.room_id)

        await ctx.stop()

    async def test_sync_removes_duplicate_from_ws_queue(
        self, mock_link_with_next: MagicMock, mock_handler: AsyncMock
    ) -> None:
        """Sync should dedupe when non-message events are ahead of sync-point WS copy."""

        participant_started = asyncio.Event()
        release_participant = asyncio.Event()
        participant_finished = asyncio.Event()

        async def handle_event(_ctx: ExecutionContext, event: PlatformEvent) -> None:
            if isinstance(event, ParticipantAddedEvent):
                participant_started.set()
                await release_participant.wait()
                participant_finished.set()

        mock_handler.side_effect = handle_event

        sync_msg = PlatformMessage(
            id="msg-sync-001",
            room_id="room-123",
            content="Sync message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        mock_link_with_next.get_next_message = AsyncMock(side_effect=[sync_msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Enqueue non-message event first so sync-point duplicate isn't queue head
        participant_event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
            type="User",
        )
        await ctx.on_event(participant_event)

        # Enqueue the same message via WS (sync-point duplicate)
        ws_event = make_message_event(room_id="room-123", msg_id="msg-sync-001")
        await ctx.on_event(ws_event)

        # Queue should contain both events
        assert ctx.queue.qsize() == 2

        async with running_execution(ctx):
            await participant_started.wait()
            assert ctx.is_processing
            assert not ctx._idle_event.is_set()
            release_participant.set()
            await participant_finished.wait()
            await ctx._idle_event.wait()
            assert not ctx.is_processing
            assert ctx.is_running
            assert ctx.queue.empty()

            # Sync point reached and duplicate removed from WS phase
            assert ctx._first_ws_msg_id is None
            assert ctx._sync_complete is True
            mock_link_with_next.mark_processing.assert_called_once_with(
                "room-123", "msg-sync-001"
            )
            mock_link_with_next.mark_processed.assert_called_once_with(
                "room-123", "msg-sync-001"
            )
            mock_link_with_next.mark_failed.assert_not_called()

            # Message handler should run once for sync message, and participant once.
            processed_message_ids = [
                call.args[1].payload.id
                for call in mock_handler.call_args_list
                if call.args[1].type == "message_created" and call.args[1].payload
            ]
            assert processed_message_ids.count("msg-sync-001") == 1
            participant_events = [
                call
                for call in mock_handler.call_args_list
                if call.args[1].type == "participant_added"
            ]
            assert len(participant_events) == 1

    async def test_ws_replay_with_processed_metadata_is_not_reopened(
        self, mock_link_with_next, mock_handler
    ):
        """Processed WebSocket replay should not call mark_processing or execute."""
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            agent_id="agent-123",
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        event = make_message_event(
            room_id="room-123",
            msg_id="msg-processed-replay",
            metadata=MessageMetadata(
                mentions=[],
                delivery_status={"agent-123": {"status": "processed"}},
            ),
        )
        await ctx.on_event(event)
        await wait_for_condition(lambda: ctx.queue.qsize() == 0)

        mock_handler.assert_not_called()
        mock_link_with_next.mark_processing.assert_not_called()
        assert "msg-processed-replay" in ctx.claims.completed_ids(ctx.room_id)

        await ctx.stop()

    async def test_ws_replay_uses_hydrated_context_delivery_status(
        self, mock_link_with_next, mock_handler
    ):
        """Stale WebSocket metadata should be checked against hydrated context."""
        context_msg = MagicMock()
        context_msg.id = "msg-stale-replay"
        context_msg.content = "already handled"
        context_msg.sender_id = "user-1"
        context_msg.sender_type = "User"
        context_msg.sender_name = "User One"
        context_msg.message_type = "text"
        context_msg.metadata = {
            "delivery_status": {"agent-123": {"status": "processed"}}
        }
        context_msg.inserted_at = "2024-01-01T00:00:00Z"
        mock_link_with_next.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[context_msg])
        )

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            agent_id="agent-123",
            config=SessionConfig(enable_context_hydration=True),
        )
        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        event = make_message_event(room_id="room-123", msg_id="msg-stale-replay")
        await ctx.on_event(event)
        await wait_for_condition(lambda: ctx.queue.qsize() == 0)

        mock_handler.assert_not_called()
        mock_link_with_next.mark_processing.assert_not_called()
        assert "msg-stale-replay" in ctx.claims.completed_ids(ctx.room_id)

        await ctx.stop()

    async def test_ws_skips_self_authored_message(
        self, mock_link_with_next, mock_handler
    ):
        """The live-event self-echo guard (band_sdk_core.is_self_echo) must
        never reach the handler or mark anything."""
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            agent_id="agent-123",
            config=SessionConfig(enable_context_hydration=False),
        )
        await ctx.start()
        await asyncio.sleep(0.05)

        event = make_message_event(
            room_id="room-123",
            msg_id="msg-self-echo",
            sender_id="agent-123",
            sender_type="Agent",
        )
        await ctx.on_event(event)
        await asyncio.sleep(0.1)

        mock_handler.assert_not_called()
        mock_link_with_next.mark_processing.assert_not_called()
        assert "msg-self-echo" not in ctx.claims.completed_ids(ctx.room_id)

        await ctx.stop()

    async def test_backlog_skips_self_authored_message(
        self, mock_link_with_next, mock_handler
    ):
        """The backlog self-echo guard (band_sdk_core.is_self_echo) must
        never reach the handler or mark anything."""

        msg = PlatformMessage(
            id="msg-backlog-self-echo",
            room_id="room-123",
            content="echo",
            sender_id="agent-123",
            sender_type="Agent",
            sender_name=None,
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            agent_id="agent-123",
            config=SessionConfig(enable_context_hydration=False),
        )

        result = await ctx._process_backlog_message(msg)

        assert result is TurnOutcome.ADVANCED
        mock_handler.assert_not_awaited()
        mock_link_with_next.mark_processing.assert_not_awaited()
        mock_link_with_next.mark_processed.assert_not_awaited()

        await ctx.stop()

    async def test_pending_next_message_present_in_context_still_executes(
        self, mock_link_with_next, mock_handler
    ):
        """A pending /next message is work even when it appears in room context."""

        pending_msg = PlatformMessage(
            id="msg-pending-down",
            room_id="room-123",
            content="Sent while agent was down",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={
                "delivery_status": {"agent-123": {"status": "pending"}},
            },
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(
            side_effect=[pending_msg, None]
        )

        context_msg = MagicMock()
        context_msg.id = "msg-pending-down"
        context_msg.content = pending_msg.content
        context_msg.sender_id = pending_msg.sender_id
        context_msg.sender_type = pending_msg.sender_type
        context_msg.sender_name = pending_msg.sender_name
        context_msg.message_type = pending_msg.message_type
        context_msg.metadata = pending_msg.metadata
        context_msg.inserted_at = "2024-01-01T00:00:00Z"
        mock_link_with_next.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[context_msg])
        )

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            agent_id="agent-123",
            config=SessionConfig(enable_context_hydration=True),
        )

        await ctx.start()
        await wait_for_condition(
            lambda: mock_link_with_next.mark_processed.call_count >= 1
        )

        mock_link_with_next.mark_processing.assert_called_once_with(
            "room-123", "msg-pending-down"
        )
        mock_handler.assert_called_once()
        assert mock_handler.call_args.args[1].payload.id == "msg-pending-down"
        mock_link_with_next.mark_processed.assert_called_once_with(
            "room-123", "msg-pending-down"
        )

        await ctx.stop()

    async def test_same_id_backlog_and_ws_paths_are_locally_inflight_deduped(
        self, mock_link_with_next, mock_handler
    ):
        """Only one path should execute when /next and WebSocket race on an id."""

        processing_started = asyncio.Event()
        release_processing = asyncio.Event()

        async def delayed_mark_processing(room_id: str, message_id: str) -> bool:
            processing_started.set()
            await release_processing.wait()
            return True

        mock_link_with_next.mark_processing = AsyncMock(
            side_effect=delayed_mark_processing
        )

        pending_msg = PlatformMessage(
            id="msg-race",
            room_id="room-123",
            content="same id",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        backlog_task = asyncio.create_task(ctx._process_backlog_message(pending_msg))
        await processing_started.wait()

        ws_event = make_message_event(room_id="room-123", msg_id="msg-race")
        await ctx._process_event(ws_event)

        assert mock_link_with_next.mark_processing.await_count == 1
        assert mock_handler.await_count == 0

        release_processing.set()
        await backlog_task

        assert mock_handler.await_count == 1
        assert ctx.claims.inflight_ids(ctx.room_id) == []

    async def test_first_message_to_fresh_room_executes_once(self, mock_link_with_next):
        """A message posted before the room's context was live executes once.

        The gateway sequence: create room, add peer, post immediately. The
        peer's startup sync receives the message from /next, and the
        WebSocket copy arrives while that execution is still in flight. The
        second delivery must be deduplicated, not re-executed.
        """

        handler_started = asyncio.Event()
        release_handler = asyncio.Event()
        handled_message_ids: list[str] = []

        async def blocking_handler(_context: ExecutionContext, event) -> None:
            handled_message_ids.append(event.payload.id)
            handler_started.set()
            await release_handler.wait()

        first_message = PlatformMessage(
            id="msg-first",
            room_id="room-123",
            content="posted before the peer subscribed",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(
            side_effect=[first_message, None]
        )

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            blocking_handler,
            agent_id="agent-123",
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.start()
        await asyncio.wait_for(handler_started.wait(), timeout=1.0)

        # WebSocket copy of the same message arrives mid-execution.
        await ctx.on_event(make_message_event(room_id="room-123", msg_id="msg-first"))

        release_handler.set()
        # Let sync finish and Phase 2 drain the WS copy.
        await wait_for_condition(
            lambda: mock_link_with_next.mark_processed.await_count >= 1
        )

        assert handled_message_ids == ["msg-first"]
        assert mock_link_with_next.mark_processing.await_count == 1
        assert mock_link_with_next.mark_processed.await_count == 1

        await ctx.stop()

    async def test_fresh_contexts_do_not_execute_the_same_message_twice(
        self, mock_link_with_next
    ):
        """Contexts sharing a runtime's registry execute a shared message once.

        The losing delivery is deferred (not treated as durably handled), so
        the resync safety net re-checks it if the owner later fails.
        """
        handler_started = asyncio.Event()
        release_handler = asyncio.Event()
        handler_calls = 0

        async def blocking_handler(_context: ExecutionContext, _event: object) -> None:
            nonlocal handler_calls
            handler_calls += 1
            handler_started.set()
            await release_handler.wait()

        registry = ClaimRegistry()

        def fresh_context() -> ExecutionContext:
            return ExecutionContext(
                "room-123",
                mock_link_with_next,
                blocking_handler,
                agent_id="agent-123",
                config=SessionConfig(
                    enable_context_hydration=False,
                    enable_working_state=False,
                ),
                claim_registry=registry,
            )

        first_context = fresh_context()
        second_context = fresh_context()
        event = make_message_event(room_id="room-123", msg_id="msg-fresh-peer")

        first_task = asyncio.create_task(first_context._process_event(event))
        await asyncio.wait_for(handler_started.wait(), timeout=1.0)
        second_task = asyncio.create_task(second_context._process_event(event))

        await asyncio.sleep(0)
        release_handler.set()
        owner_handled, duplicate_handled = await asyncio.gather(first_task, second_task)

        assert handler_calls == 1
        assert mock_link_with_next.mark_processing.await_count == 1
        assert owner_handled is TurnOutcome.ADVANCED
        assert duplicate_handled is TurnOutcome.RETRY_LATER

    async def test_mark_processing_failure_does_not_execute_message(
        self, mock_link_with_next, mock_handler
    ):
        """If durable processing claim fails, adapter execution must not start."""
        mock_link_with_next.mark_processing = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        event = make_message_event(room_id="room-123", msg_id="msg-claim-fails")
        await ctx._process_event(event)

        mock_link_with_next.mark_processing.assert_awaited_once_with(
            "room-123", "msg-claim-fails"
        )
        mock_handler.assert_not_called()
        mock_link_with_next.mark_processed.assert_not_called()
        assert ctx.claims.inflight_ids(ctx.room_id) == []

    async def test_handler_failure_marks_failed_and_releases_claim(
        self, mock_link_with_next
    ):
        """A real handler failure must not strand ownership of the message."""

        async def failing_handler(ctx, event):
            raise RuntimeError("handler failed")

        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_failed = AsyncMock(return_value=True)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            failing_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        event = make_message_event(room_id="room-123", msg_id="msg-handler-fails")

        assert await ctx._process_event(event) is TurnOutcome.ADVANCED

        mock_link_with_next.mark_failed.assert_awaited_once_with(
            "room-123", "msg-handler-fails", "handler failed"
        )
        assert ctx.claims.inflight_ids(ctx.room_id) == []

    async def test_backlog_processed_ack_failure_is_not_remembered(
        self, mock_link_with_next, mock_handler
    ):
        """Local success without durable processed ack must not enter processed dedupe."""

        msg = PlatformMessage(
            id="msg-ack-fails",
            room_id="room-123",
            content="ack fails",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        result = await ctx._process_backlog_message(msg)

        assert result is TurnOutcome.RETRY_LATER
        mock_handler.assert_awaited_once()
        mock_link_with_next.mark_processed.assert_awaited_once_with(
            "room-123", "msg-ack-fails"
        )
        assert "msg-ack-fails" not in ctx.claims.completed_ids(ctx.room_id)
        assert ctx.claims.is_ack_pending(ctx.room_id, "msg-ack-fails")
        assert ctx.claims.inflight_ids(ctx.room_id) == []

    async def test_websocket_processed_ack_failure_is_not_remembered(
        self, mock_link_with_next, mock_handler
    ):
        """WebSocket success without durable processed ack must not enter dedupe."""
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        event = make_message_event(room_id="room-123", msg_id="msg-ws-ack-fails")
        await ctx._process_event(event)

        mock_handler.assert_awaited_once()
        mock_link_with_next.mark_processed.assert_awaited_once_with(
            "room-123", "msg-ws-ack-fails"
        )
        assert "msg-ws-ack-fails" not in ctx.claims.completed_ids(ctx.room_id)
        assert ctx.claims.is_ack_pending(ctx.room_id, "msg-ws-ack-fails")

    async def test_backlog_processed_ack_failure_retries_ack_without_handler_replay(
        self, mock_link_with_next, mock_handler
    ):
        """Redelivery after local success should retry only the processed ack."""

        msg = PlatformMessage(
            id="msg-backlog-ack-retry",
            room_id="room-123",
            content="ack retry",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(side_effect=[False, True])
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        assert await ctx._process_backlog_message(msg) is TurnOutcome.RETRY_LATER
        assert await ctx._process_backlog_message(msg) is TurnOutcome.ADVANCED

        mock_handler.assert_awaited_once()
        assert mock_link_with_next.mark_processed.await_count == 2
        assert "msg-backlog-ack-retry" in ctx.claims.completed_ids(ctx.room_id)
        assert not ctx.claims.is_ack_pending(ctx.room_id, "msg-backlog-ack-retry")

    async def test_processed_ack_retry_budget_exhaustion_keeps_local_completion(
        self, mock_link_with_next, mock_handler
    ):
        """Permanent processed ack failure should not deadlock or replay local side effects."""

        msg = PlatformMessage(
            id="msg-ack-budget",
            room_id="room-123",
            content="ack budget",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(
                enable_context_hydration=False,
                max_message_retries=1,
            ),
        )

        assert await ctx._process_backlog_message(msg) is TurnOutcome.RETRY_LATER
        assert await ctx._process_backlog_message(msg) is TurnOutcome.ADVANCED

        mock_handler.assert_awaited_once()
        assert mock_link_with_next.mark_processed.await_count == 2
        assert "msg-ack-budget" in ctx.claims.completed_ids(ctx.room_id)
        assert not ctx.claims.is_ack_pending(ctx.room_id, "msg-ack-budget")

    async def test_websocket_processed_ack_failure_retries_ack_without_handler_replay(
        self, mock_link_with_next, mock_handler
    ):
        """WebSocket redelivery after local success should retry only the processed ack."""
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(side_effect=[False, True])
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        event = make_message_event(room_id="room-123", msg_id="msg-ws-ack-retry")
        await ctx._process_event(event)
        await ctx._process_event(event)

        mock_handler.assert_awaited_once()
        assert mock_link_with_next.mark_processed.await_count == 2
        assert "msg-ws-ack-retry" in ctx.claims.completed_ids(ctx.room_id)
        assert not ctx.claims.is_ack_pending(ctx.room_id, "msg-ws-ack-retry")

    async def test_ack_pending_message_survives_lru_pressure_without_replay(
        self, mock_link_with_next, mock_handler
    ):
        """Cache pressure must never evict ACK_PENDING into a side-effect replay.

        A message whose handler completed but whose durable processed ack
        failed may only retry the ack on redelivery — even after enough later
        completions to overflow the dedupe cache.
        """
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            agent_id="agent-123",
            config=SessionConfig(
                enable_context_hydration=False,
                enable_working_state=False,
                # Retry headroom so eviction shows up as a handler replay,
                # not as the retry budget silently dropping the redelivery.
                max_message_retries=5,
            ),
        )

        async def deliver(message_id: str) -> None:
            await ctx._process_event(
                make_message_event(room_id="room-123", msg_id=message_id)
            )

        # Handler completes but the durable ack fails → ACK_PENDING.
        await deliver("msg-unacked")
        # Enough later completions to overflow the dedupe cache capacity.
        for i in range(ctx.claims.max_completed):
            await deliver(f"msg-filler-{i}")

        executions_before_redelivery = mock_handler.await_count
        mock_link_with_next.mark_processed = AsyncMock(return_value=True)

        await deliver("msg-unacked")

        assert mock_handler.await_count == executions_before_redelivery
        mock_link_with_next.mark_processed.assert_awaited_once_with(
            "room-123", "msg-unacked"
        )

    async def test_websocket_processed_ack_failure_retries_ack_before_newer_queue(
        self, mock_link_with_next, mock_handler
    ):
        """The process loop should retry a failed WebSocket processed ack before newer events."""
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(side_effect=[False, True, True])
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await ctx.on_event(make_message_event(room_id="room-123", msg_id="msg-old-ws"))
        await ctx.on_event(make_message_event(room_id="room-123", msg_id="msg-new-ws"))
        await wait_for_condition(
            lambda: mock_link_with_next.mark_processed.await_count >= 3
        )

        assert [call.args[1].payload.id for call in mock_handler.await_args_list] == [
            "msg-old-ws",
            "msg-new-ws",
        ]
        assert mock_link_with_next.mark_processed.await_count == 3
        assert "msg-old-ws" in ctx.claims.completed_ids(ctx.room_id)
        assert "msg-new-ws" in ctx.claims.completed_ids(ctx.room_id)
        assert ctx.claims.pending_ack_ids(ctx.room_id) == []

        await ctx.stop()

    async def test_resync_retries_pending_ack_before_advancing_to_newer_backlog(
        self, mock_link_with_next, mock_handler
    ):
        """_wait_until_resync_complete (the backlog-side resync loop, distinct
        from the WebSocket-queue path above) must retry a stuck pending ACK
        before processing a newer /next backlog message -- normal resync
        cannot get past a stuck pending ACK to reach newer backlog. Once the
        ACK confirms, resync proceeds normally to the newer message."""

        newer_msg = PlatformMessage(
            id="msg-newer",
            room_id="room-123",
            content="newer",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_processed = AsyncMock(return_value=True)
        mock_link_with_next.get_next_message = AsyncMock(side_effect=[newer_msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        ctx.claims.remember_ack_pending("room-123", "msg-stuck")

        await ctx._wait_until_resync_complete()

        # The stuck ACK is retried before the newer backlog message is fetched.
        assert mock_link_with_next.mark_processed.await_args_list[0].args == (
            "room-123",
            "msg-stuck",
        )
        assert "msg-stuck" in ctx.claims.completed_ids("room-123")
        # The handler only ever ran for the newer message -- the stuck entry's
        # ACK retry never replays it.
        mock_handler.assert_awaited_once()
        assert "msg-newer" in ctx.claims.completed_ids("room-123")

    async def test_sync_point_claim_failure_does_not_clear_marker(
        self, mock_link_with_next, mock_handler
    ):
        """A failed durable claim is not a completed sync point."""

        sync_msg = PlatformMessage(
            id="msg-sync-claim-fails",
            room_id="room-123",
            content="sync claim fails",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(return_value=sync_msg)
        mock_link_with_next.mark_processing = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.on_event(
            make_message_event(room_id="room-123", msg_id="msg-sync-claim-fails")
        )
        await ctx.start()
        await wait_for_condition(
            lambda: mock_link_with_next.mark_processing.await_count >= 1
        )

        assert ctx._first_ws_msg_id == "msg-sync-claim-fails"
        mock_handler.assert_not_called()
        assert "msg-sync-claim-fails" not in ctx.claims.completed_ids(ctx.room_id)

        await ctx.stop()

    async def test_startup_backlog_claim_failure_does_not_spin(
        self, mock_link_with_next, mock_handler
    ):
        """Startup sync should stop after one unclaimable non-sync backlog message."""

        msg = PlatformMessage(
            id="msg-startup-claim-fails",
            room_id="room-123",
            content="startup claim fails",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(return_value=msg)
        mock_link_with_next.mark_processing = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        synchronized = await ctx._synchronize_with_next()

        assert synchronized is False
        assert ctx._sync_complete is False
        mock_link_with_next.get_next_message.assert_awaited_once()
        mock_link_with_next.mark_processing.assert_awaited_once_with(
            "room-123", "msg-startup-claim-fails"
        )
        mock_handler.assert_not_called()
        assert "msg-startup-claim-fails" not in ctx.claims.completed_ids(ctx.room_id)

    async def test_refused_claims_never_spend_the_retry_budget(
        self, mock_link_with_next, mock_handler
    ):
        """A platform that refuses claims under load must not poison the message:
        it runs once a claim finally lands, however many refusals came first."""
        msg = PlatformMessage(
            id="msg-claim-refused-under-load",
            room_id="room-123",
            content="claim refused under load",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(side_effect=[msg] * 4 + [None])
        mock_link_with_next.mark_processing = AsyncMock(
            side_effect=[False, False, False, True]
        )
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False, max_message_retries=1),
        )

        for _ in range(3):
            assert await ctx._synchronize_with_next() is False
        assert await ctx._synchronize_with_next() is True

        mock_handler.assert_awaited_once()
        assert not ctx._retry_tracker.is_permanently_failed(msg.id)

    async def test_startup_backlog_claim_failure_does_not_process_newer_ws_event(
        self, mock_link_with_next, mock_handler
    ):
        """Startup sync should not switch to WebSocket after an unclaimable backlog message."""

        backlog_msg = PlatformMessage(
            id="msg-older-claim-fails",
            room_id="room-123",
            content="older claim fails",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(return_value=backlog_msg)
        mock_link_with_next.mark_processing = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(
                enable_context_hydration=False,
                idle_resync_seconds=10,
            ),
        )

        await ctx.on_event(
            make_message_event(room_id="room-123", msg_id="msg-newer-ws")
        )
        await ctx.start()
        await wait_for_condition(
            lambda: mock_link_with_next.mark_processing.await_count >= 1
        )

        mock_link_with_next.mark_processing.assert_awaited_once_with(
            "room-123", "msg-older-claim-fails"
        )
        mock_handler.assert_not_called()
        assert ctx._sync_complete is False
        assert ctx._first_ws_msg_id == "msg-newer-ws"

        await ctx.stop()

    async def test_resync_claim_failure_does_not_spin(
        self, mock_link_with_next, mock_handler
    ):
        """Resync should stop after one unclaimable /next message."""

        msg = PlatformMessage(
            id="msg-resync-claim-fails",
            room_id="room-123",
            content="resync claim fails",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(return_value=msg)
        mock_link_with_next.mark_processing = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        synchronized = await ctx._resync_pending_messages()

        assert synchronized is ResyncOutcome.RETRY
        mock_link_with_next.get_next_message.assert_awaited_once()
        mock_link_with_next.mark_processing.assert_awaited_once_with(
            "room-123", "msg-resync-claim-fails"
        )
        mock_handler.assert_not_called()
        assert "msg-resync-claim-fails" not in ctx.claims.completed_ids(ctx.room_id)

    async def test_resync_claim_failure_does_not_process_newer_ws_event(
        self, mock_link_with_next, mock_handler
    ):
        """Phase 2 resync should block queued WebSocket events behind older /next work."""

        msg = PlatformMessage(
            id="msg-resync-older-claim-fails",
            room_id="room-123",
            content="resync older claim fails",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.get_next_message = AsyncMock(side_effect=[None, msg])
        mock_link_with_next.mark_processing = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(
                enable_context_hydration=False,
                idle_resync_seconds=10,
            ),
        )

        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)
        await ctx.request_resync()
        await ctx.on_event(
            make_message_event(room_id="room-123", msg_id="msg-resync-newer-ws")
        )
        await wait_for_condition(
            lambda: mock_link_with_next.mark_processing.await_count >= 1
        )

        mock_link_with_next.mark_processing.assert_awaited_once_with(
            "room-123", "msg-resync-older-claim-fails"
        )
        mock_handler.assert_not_called()
        assert "msg-resync-newer-ws" not in ctx.claims.completed_ids(ctx.room_id)

        await ctx.stop()

    async def test_sync_skips_permanently_failed(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should skip permanently failed messages."""

        failed_msg = PlatformMessage(
            id="msg-failed-001",
            room_id="room-123",
            content="Failed message",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        mock_link_with_next.get_next_message = AsyncMock(side_effect=[failed_msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Mark message as permanently failed
        ctx._retry_tracker.mark_permanently_failed("msg-failed-001")

        await ctx.start()
        await wait_for_condition(lambda: ctx._sync_complete)

        # Handler should NOT be called for failed message
        assert mock_handler.call_count == 0

        await ctx.stop()

    async def test_retry_tracker_records_failures(self, mock_link_with_next):
        """Retry tracker should record failed processing attempts."""

        # Handler that fails
        failing_handler = AsyncMock(side_effect=Exception("Processing failed"))

        msg = PlatformMessage(
            id="msg-001",
            room_id="room-123",
            content="Test",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        mock_link_with_next.get_next_message = AsyncMock(side_effect=[msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            failing_handler,
            config=SessionConfig(enable_context_hydration=False, max_message_retries=2),
        )

        await ctx.start()
        await asyncio.sleep(0.2)

        # Should have recorded attempt
        # Note: With max_retries=2, after 3 attempts it's permanently failed
        # But we only process once per /next call
        await ctx.stop()

    async def test_retry_saturation_skips_handler_on_next_delivery(
        self, mock_link_with_next
    ):
        """Once a message's attempts exceed max_retries it becomes permanently
        failed, and a *subsequent* delivery of that same message must skip the
        handler entirely rather than invoke it again."""

        failing_handler = AsyncMock(side_effect=Exception("Processing failed"))
        msg = PlatformMessage(
            id="msg-saturates",
            room_id="room-123",
            content="Test",
            sender_id="user-1",
            sender_type="User",
            sender_name="User One",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )
        mock_link_with_next.mark_processing = AsyncMock(return_value=True)
        mock_link_with_next.mark_failed = AsyncMock(return_value=True)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            failing_handler,
            config=SessionConfig(enable_context_hydration=False, max_message_retries=1),
        )

        # Attempt 1 (attempts=1, within max_retries=1) invokes the handler and
        # fails. Attempt 2 (attempts=2, exceeds max_retries=1) is the allowed
        # budget's last attempt getting saturated -- record_attempt reports
        # exceeded before the handler would run, so it is skipped here too.
        await ctx._process_backlog_message(msg)
        await ctx._process_backlog_message(msg)
        assert failing_handler.await_count == 1
        assert ctx._retry_tracker.is_permanently_failed("msg-saturates")

        # A further delivery of the same message must not invoke the handler.
        result = await ctx._process_backlog_message(msg)

        assert result is TurnOutcome.ADVANCED
        assert failing_handler.await_count == 1
