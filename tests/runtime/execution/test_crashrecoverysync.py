from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from band_sdk_core import ClaimRegistry

from band.client.streaming import DeliveryStatus
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.platform.message_lifecycle import ActionablePage
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from band.runtime.messages import PlatformMessage
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_message_event,
    make_participant_added_event,
    mirror_settlement_results,
)
from tests.runtime.batching.kit import Turns
from tests.runtime.conftest import Waiting, wait_for_condition
from tests.runtime.delivery.kit import (
    RestPeer,
    room_over,
    started_session,
    turn,
    write,
)
from tests.runtime.helpers import AGENT_ID
from tests.runtime.scheduler.driver import attempt_delivery


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
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        mirror_settlement_results(link)
        link.get_next_message = AsyncMock(return_value=None)  # No backlog by default
        link.get_actionable_page = AsyncMock(return_value=ActionablePage([], None))
        link.get_stale_processing_messages = AsyncMock(return_value=[])  # No stale msgs
        link.report_activity = AsyncMock(return_value=True)

        return link

    async def test_first_ws_message_sets_marker(
        self, mock_link_with_next, mock_handler
    ):
        """First WebSocket message should set sync point marker."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        assert ctx.scheduler.first_ws_id is None

        event = make_message_event(room_id="room-123", msg_id="msg-ws-001")
        await ctx.on_event(event)

        ctx.scheduler.absorb()
        assert ctx.scheduler.first_ws_id == "msg-ws-001"

    async def test_subsequent_ws_messages_dont_change_marker(
        self, mock_link_with_next, mock_handler
    ):
        """Subsequent WebSocket messages should not change the marker."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        event1 = make_message_event(room_id="room-123", msg_id="msg-ws-001")
        event2 = make_message_event(room_id="room-123", msg_id="msg-ws-002")

        await ctx.on_event(event1)
        await ctx.on_event(event2)

        ctx.scheduler.absorb()
        assert ctx.scheduler.first_ws_id == "msg-ws-001"

    async def test_sync_completes_with_no_backlog(
        self, mock_link_with_next, mock_handler
    ):
        """Sync should complete immediately when no backlog."""
        ctx = ExecutionContext("room-123", mock_link_with_next, mock_handler)

        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        assert ctx.scheduler.sync_complete is True
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
        ctx.scheduler.absorb()
        assert ctx.scheduler.first_ws_id == "msg-sync-001"

        # Start should sync and find sync point
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        # Marker should be cleared
        assert ctx.scheduler.first_ws_id is None
        # Dedupe cache should keep processed sync id to avoid WS reprocessing
        assert "msg-sync-001" in ctx.claims.completed_ids(ctx.room_id)

        await ctx.stop()

    @pytest.mark.looptime
    async def test_sync_removes_duplicate_from_ws_queue(self):
        """A backlog message the WebSocket also delivered runs once; so does the roster event."""
        peer = RestPeer()
        original = peer.post("msg-sync", at=1)
        turns = Turns()
        async with room_over(peer, turns.single) as ctx:
            await ctx.on_event(make_participant_added_event(participant_id="user-2"))
            await ctx.on_event(original)
            await ctx.start()
            await wait_for_condition(lambda: len(turns.ids) == 2)
            await Waiting.quiesce()

        assert sorted(turns.ids) == [("msg-sync",), ("participant_added",)]
        assert peer.without_reads() == [
            write(DeliveryStatus.PROCESSING, "msg-sync"),
            write(DeliveryStatus.PROCESSED, "msg-sync"),
        ]

    @pytest.mark.looptime
    async def test_ws_replay_with_processed_metadata_is_not_reopened(self):
        """A WebSocket replay the platform already shows processed neither runs nor writes."""
        peer = RestPeer()
        peer.post("msg-processed-replay", at=1, status=DeliveryStatus.PROCESSED)
        async with started_session(peer) as session:
            await session.quiesce()
            await session.deliver(peer.replay("msg-processed-replay"))
            await session.quiesce()

        assert session.turns.ids == []
        assert peer.without_reads() == []

    @pytest.mark.looptime
    async def test_ws_skips_self_authored_message(self):
        """The agent's own message, delivered live, neither runs nor writes."""
        peer = RestPeer()
        async with started_session(peer) as session:
            await session.quiesce()
            await session.deliver(
                peer.post(
                    "msg-self-echo", at=1, sender_id=AGENT_ID, sender_type="Agent"
                )
            )
            await session.quiesce()

        assert session.turns.ids == []
        assert peer.without_reads() == []

    @pytest.mark.looptime
    async def test_backlog_skips_self_authored_message(self):
        """The agent's own message in the backlog neither runs nor writes."""
        peer = RestPeer()
        peer.post(
            "msg-backlog-self-echo", at=1, sender_id=AGENT_ID, sender_type="Agent"
        )
        async with started_session(peer) as session:
            await session.quiesce()

        assert session.turns.ids == []
        assert peer.without_reads() == []

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

        mock_handler.assert_called_once()
        assert mock_handler.call_args.args[1].payload.id == "msg-pending-down"
        mock_link_with_next.mark_processed.assert_called_once_with(
            "room-123", "msg-pending-down"
        )

        await ctx.stop()

    @pytest.mark.looptime
    async def test_same_id_backlog_and_ws_paths_are_locally_inflight_deduped(self):
        """Only one path executes when /next and the WebSocket race on an id."""
        peer = RestPeer()
        original = peer.post("msg-race", at=1)
        claiming, release = asyncio.Event(), asyncio.Event()
        answer = peer.answer

        async def held_claim(request: httpx.Request) -> httpx.Response:
            response = answer(request)
            if request.url.path.endswith("/processing"):
                claiming.set()
                await release.wait()
            return response

        peer.answer = held_claim
        turns = Turns()
        async with room_over(peer, turns.single) as ctx:
            backlog = asyncio.create_task(attempt_delivery(ctx, peer.row("msg-race")))
            await claiming.wait()
            await attempt_delivery(ctx, original)
            while_claiming = (list(turns.ids), list(peer.without_reads()))
            release.set()
            await backlog

        assert while_claiming == ([], [write(DeliveryStatus.PROCESSING, "msg-race")])
        assert turns.ids == [("msg-race",)]
        assert peer.without_reads() == [
            write(DeliveryStatus.PROCESSING, "msg-race"),
            write(DeliveryStatus.PROCESSED, "msg-race"),
        ]

    @pytest.mark.looptime
    async def test_first_message_to_fresh_room_executes_once(self):
        """A message posted before the room's context was live executes once.

        The gateway sequence: create room, add peer, post immediately. The
        peer's startup sync receives the message from /next, and the
        WebSocket copy arrives while that execution is still in flight. The
        second delivery must be deduplicated, not re-executed.
        """
        peer = RestPeer()
        original = peer.post("msg-first", at=1)
        async with started_session(peer, gate=True) as session:
            await session.until(lambda: session.turns.ids == [("msg-first",)])
            await session.deliver(original)
            session.turns.release.set()
            await session.settled("msg-first")
            await session.quiesce()

        assert session.turns.ids == [("msg-first",)]
        assert peer.without_reads() == [
            write(DeliveryStatus.PROCESSING, "msg-first"),
            turn("msg-first"),
            write(DeliveryStatus.PROCESSED, "msg-first"),
        ]

    @pytest.mark.looptime
    async def test_fresh_contexts_do_not_execute_the_same_message_twice(self):
        """Contexts sharing a runtime's registry execute a shared message once.

        The losing delivery is deferred (not treated as durably handled), so
        the resync safety net re-checks it if the owner later fails.
        """
        peer = RestPeer()
        original = peer.post("msg-fresh-peer", at=1)
        registry = ClaimRegistry()
        turns = Turns(gate=True)
        async with (
            room_over(peer, turns.single, claims=registry) as first,
            room_over(peer, turns.single, claims=registry) as second,
        ):
            owner = asyncio.create_task(attempt_delivery(first, original))
            await wait_for_condition(lambda: turns.ids == [("msg-fresh-peer",)])
            duplicate = await attempt_delivery(second, original)
            turns.release.set()
            owned = await owner

        assert (owned, duplicate) == (TurnOutcome.ADVANCED, TurnOutcome.RETRY_LATER)
        assert turns.ids == [("msg-fresh-peer",)]
        assert peer.without_reads() == [
            write(DeliveryStatus.PROCESSING, "msg-fresh-peer"),
            write(DeliveryStatus.PROCESSED, "msg-fresh-peer"),
        ]

    @pytest.mark.looptime
    async def test_mark_processing_failure_does_not_execute_message(self):
        """If the durable processing claim fails, adapter execution must not start."""
        peer = RestPeer()
        original = peer.post("msg-claim-fails", at=1)
        peer.refuse_processing.add("msg-claim-fails")
        turns = Turns()
        async with room_over(peer, turns.single) as ctx:
            await attempt_delivery(ctx, original)

        assert turns.ids == []
        assert peer.without_reads() == [
            write(DeliveryStatus.PROCESSING, "msg-claim-fails", confirmed=False)
        ]

    async def test_handler_failure_marks_failed_and_releases_claim(
        self, mock_link_with_next
    ):
        """A real handler failure must not strand ownership of the message."""

        async def failing_handler(ctx, event):
            raise RuntimeError("handler failed")

        mock_link_with_next.mark_failed = AsyncMock(return_value=True)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            failing_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        event = make_message_event(room_id="room-123", msg_id="msg-handler-fails")

        assert await attempt_delivery(ctx, event) is TurnOutcome.ADVANCED

        mock_link_with_next.mark_failed.assert_awaited_once_with(
            "room-123", "msg-handler-fails", GENERIC_PROVIDER_FAILURE_MESSAGE
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
        mock_link_with_next.mark_processed = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        result = await attempt_delivery(ctx, msg)

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
        mock_link_with_next.mark_processed = AsyncMock(return_value=False)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        event = make_message_event(room_id="room-123", msg_id="msg-ws-ack-fails")
        await attempt_delivery(ctx, event)

        mock_handler.assert_awaited_once()
        mock_link_with_next.mark_processed.assert_awaited_once_with(
            "room-123", "msg-ws-ack-fails"
        )
        assert "msg-ws-ack-fails" not in ctx.claims.completed_ids(ctx.room_id)
        assert ctx.claims.is_ack_pending(ctx.room_id, "msg-ws-ack-fails")

    @pytest.mark.looptime
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
        mock_link_with_next.mark_processed = AsyncMock(side_effect=[False, True])
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        assert await attempt_delivery(ctx, msg) is TurnOutcome.RETRY_LATER
        await asyncio.sleep(ctx.config.idle_resync_seconds)
        assert await attempt_delivery(ctx, msg) is TurnOutcome.ADVANCED

        mock_handler.assert_awaited_once()
        assert mock_link_with_next.mark_processed.await_count == 2
        assert "msg-backlog-ack-retry" in ctx.claims.completed_ids(ctx.room_id)
        assert not ctx.claims.is_ack_pending(ctx.room_id, "msg-backlog-ack-retry")

    @pytest.mark.looptime
    async def test_ack_retry_budget_exhaustion_keeps_the_intent_pending(
        self, mock_link_with_next, mock_handler
    ):
        """An unconfirmed terminal write is retained, not dropped, once its
        retry budget is spent: dropping it would let the room replay effects."""

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
        mock_link_with_next.mark_processed = AsyncMock(
            side_effect=[False, False, False, True]
        )
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(
                enable_context_hydration=False,
                max_message_retries=1,
            ),
        )

        outcomes = []
        for _ in range(4):
            outcomes.append(await attempt_delivery(ctx, msg))
            await asyncio.sleep(ctx.config.idle_resync_seconds)

        assert outcomes == [
            TurnOutcome.RETRY_LATER,
            TurnOutcome.RETRY_LATER,
            TurnOutcome.RETRY_LATER,
            TurnOutcome.ADVANCED,
        ]
        mock_handler.assert_awaited_once()
        assert mock_link_with_next.mark_processed.await_count == 4
        assert "msg-ack-budget" in ctx.claims.completed_ids(ctx.room_id)
        assert not ctx.claims.is_ack_pending(ctx.room_id, "msg-ack-budget")

    @pytest.mark.looptime
    async def test_websocket_processed_ack_failure_retries_ack_without_handler_replay(
        self, mock_link_with_next, mock_handler
    ):
        """WebSocket redelivery after local success should retry only the processed ack."""
        mock_link_with_next.mark_processed = AsyncMock(side_effect=[False, True])
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        event = make_message_event(room_id="room-123", msg_id="msg-ws-ack-retry")
        await attempt_delivery(ctx, event)
        await asyncio.sleep(ctx.config.idle_resync_seconds)
        await attempt_delivery(ctx, event)

        mock_handler.assert_awaited_once()
        assert mock_link_with_next.mark_processed.await_count == 2
        assert "msg-ws-ack-retry" in ctx.claims.completed_ids(ctx.room_id)
        assert not ctx.claims.is_ack_pending(ctx.room_id, "msg-ws-ack-retry")

    @pytest.mark.looptime
    async def test_ack_pending_message_survives_lru_pressure_without_replay(
        self, mock_link_with_next, mock_handler
    ):
        """Cache pressure must never evict ACK_PENDING into a side-effect replay.

        A message whose handler completed but whose durable processed ack
        failed may only retry the ack on redelivery — even after enough later
        completions to overflow the dedupe cache.
        """
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
            await attempt_delivery(
                ctx, make_message_event(room_id="room-123", msg_id=message_id)
            )

        # Handler completes but the durable ack fails → ACK_PENDING.
        await deliver("msg-unacked")
        # Enough later completions to overflow the dedupe cache capacity.
        for i in range(ctx.claims.max_completed):
            await deliver(f"msg-filler-{i}")

        executions_before_redelivery = mock_handler.await_count
        mock_link_with_next.mark_processed = AsyncMock(return_value=True)

        await asyncio.sleep(ctx.config.idle_resync_seconds)
        await deliver("msg-unacked")

        assert mock_handler.await_count == executions_before_redelivery
        mock_link_with_next.mark_processed.assert_awaited_once_with(
            "room-123", "msg-unacked"
        )

    @pytest.mark.looptime
    async def test_websocket_processed_ack_failure_is_retried_by_the_loop_without_replay(
        self,
    ):
        """The process loop retries a failed WebSocket processed ack on its next
        reconciliation, however many newer events ran in the meantime."""
        peer = RestPeer()
        async with started_session(peer) as session:
            await session.quiesce()
            peer.confirm_terminal = False
            await session.deliver(peer.post("old-ws", at=1))
            await session.until(
                lambda: bool(peer.writes_of(DeliveryStatus.PROCESSED, "old-ws"))
            )
            peer.confirm_terminal = True
            await session.deliver(peer.post("new-ws", at=2))
            # One clock jump avoids thousands of polling timers before reconciliation.
            await asyncio.sleep(2 * session.ctx.config.idle_resync_seconds)
            assert peer.status_of("old-ws") is DeliveryStatus.PROCESSED

        assert session.turns.ids == [("old-ws",), ("new-ws",)]
        assert peer.status_of("new-ws") is DeliveryStatus.PROCESSED
        assert len(peer.writes_of(DeliveryStatus.PROCESSED, "old-ws")) == 2

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
        mock_link_with_next.mark_processed = AsyncMock(return_value=True)
        mock_link_with_next.get_next_message = AsyncMock(side_effect=[newer_msg, None])

        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )
        ctx.claims.remember_ack_pending("room-123", "msg-stuck")

        await ctx.scheduler.run_pass(recovery=True)

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

    @pytest.mark.looptime
    async def test_startup_backlog_claim_failure_does_not_spin(self):
        """Startup sync makes one read and one claim for an unclaimable backlog head."""
        peer = RestPeer()
        peer.post("older", at=1)
        peer.refuse_processing.add("older")
        async with started_session(peer) as session:
            await session.quiesce()

        assert session.turns.ids == []
        assert (len(peer.nexts), session.admission_attempts) == (1, ["older"])

    @pytest.mark.looptime
    async def test_refused_claims_never_spend_the_retry_budget(self):
        """A platform that refuses claims under load must not poison the message:
        it runs once a claim finally lands, however many refusals came first."""
        peer = RestPeer()
        peer.post("msg-claim-refused-under-load", at=1)
        peer.refuse_processing.add("msg-claim-refused-under-load")
        async with started_session(peer, retries=1) as session:
            await session.quiesce()
            for _ in range(3):
                await session.resync()
            refused = list(session.admission_attempts)
            peer.refuse_processing.clear()
            await session.resync()
            await session.settled("msg-claim-refused-under-load")

        assert refused == ["msg-claim-refused-under-load"] * 4
        assert session.turns.ids == [("msg-claim-refused-under-load",)]

    @pytest.mark.looptime
    async def test_startup_backlog_claim_failure_does_not_process_newer_ws_event(
        self,
    ):
        """Startup sync should not switch to WebSocket after an unclaimable backlog message."""
        peer = RestPeer()
        peer.post("older", at=1)
        peer.refuse_processing.add("older")
        async with started_session(peer) as session:
            await session.deliver(peer.post("newer", at=2))
            await session.until(lambda: "older" in session.admission_attempts)
            await session.quiesce()

        assert session.turns.ids == []
        assert set(session.admission_attempts) == {"older"}
        assert peer.status_of("newer") is None

    @pytest.mark.looptime
    async def test_resync_claim_failure_does_not_spin(self):
        """A resync makes one read and one claim for an unclaimable /next head."""
        peer = RestPeer()
        async with started_session(peer) as session:
            await session.quiesce()
            peer.post("older", at=1)
            peer.refuse_processing.add("older")
            reads_before = len(peer.nexts)
            await session.resync()

        assert session.turns.ids == []
        assert (len(peer.nexts) - reads_before, session.admission_attempts) == (
            1,
            ["older"],
        )

    @pytest.mark.looptime
    async def test_resync_claim_failure_does_not_process_newer_ws_event(self):
        """Resync should block queued WebSocket events behind older /next work."""
        peer = RestPeer()
        async with started_session(peer) as session:
            await session.quiesce()
            # A message the socket missed, which the platform will not let the room claim.
            peer.post("older", at=1)
            peer.refuse_processing.add("older")
            await session.ctx.request_resync()
            await session.deliver(peer.post("newer", at=2))
            await session.until(lambda: "older" in session.admission_attempts)
            await session.quiesce()

        assert session.turns.ids == []
        assert set(session.admission_attempts) == {"older"}
        assert peer.status_of("newer") is None

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
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

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
        mock_link_with_next.mark_failed = AsyncMock(return_value=True)
        ctx = ExecutionContext(
            "room-123",
            mock_link_with_next,
            failing_handler,
            config=SessionConfig(enable_context_hydration=False, max_message_retries=1),
        )

        # Confirmation exhausts this original's only permitted execution.
        await attempt_delivery(ctx, msg)
        await attempt_delivery(ctx, msg)
        assert failing_handler.await_count == 1
        mock_link_with_next.mark_failed.assert_awaited_once_with(
            "room-123", "msg-saturates", GENERIC_PROVIDER_FAILURE_MESSAGE
        )

        # A further delivery of the same message must not invoke the handler.
        result = await attempt_delivery(ctx, msg)

        assert result is TurnOutcome.ADVANCED
        assert failing_handler.await_count == 1
