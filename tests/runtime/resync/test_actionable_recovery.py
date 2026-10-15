"""Complete actionable snapshots behind retained heads, with FIFO admission."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from band.client.streaming import ControlMode
from band.core.exceptions import InputNotAccepted
from band.platform.link import BandLink
from band.runtime.execution import ExecutionContext, ResyncOutcome
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event, make_participant_added_event
from tests.runtime.helpers import AGENT_ID, ROOM_ID, rest_client_over
from tests.runtime.resync.kit import BacklogApi


class TestActionableRecovery:
    """A retained actionable head must not hide or overtake eligible work."""

    @staticmethod
    def context(
        link: BandLink, handler: Callable[[ExecutionContext, Any], Awaitable[None]]
    ) -> ExecutionContext:
        return ExecutionContext(
            ROOM_ID,
            link,
            handler,
            agent_id=AGENT_ID,
            config=SessionConfig(
                enable_context_hydration=False,
                enable_working_state=False,
                report_turn_failures_to_room=False,
                max_message_retries=2,
            ),
        )

    @staticmethod
    async def recover(ctx: ExecutionContext, phase: str) -> bool | ResyncOutcome:
        async with asyncio.timeout(1):
            if phase == "bootstrap":
                return await ctx._synchronize_with_next()
            return await ctx._resync_pending_messages()

    @pytest.mark.parametrize("trigger", ["exhausted", "stale"])
    async def test_snapshot_hands_off_at_websocket_marker(
        self, backlog: tuple[BandLink, BacklogApi], trigger: str
    ) -> None:
        link, peer = backlog
        if trigger == "exhausted":
            peer.add("exhausted", "failed")
        peer.add("A", "processing" if trigger == "stale" else "pending")
        peer.add("B", "pending")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="A"))
        await ctx.on_event(
            make_participant_added_event(room_id=ROOM_ID, participant_id="new-user")
        )
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="B"))

        assert await self.recover(ctx, "bootstrap") is True
        while not ctx.queue.empty():
            await ctx._process_event(ctx.queue.get_nowait())

        assert seen == ["A", "new-user", "B"]

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("mode", list(ControlMode))
    @pytest.mark.parametrize("snapshot", ["rows", "empty"])
    async def test_control_during_snapshot_invalidates_even_empty_response(
        self,
        backlog: tuple[BandLink, BacklogApi],
        phase: str,
        mode: ControlMode,
        snapshot: str,
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("older", "pending")
        peer.add("stale", "processing")
        entered = asyncio.Event()
        release = asyncio.Event()
        seen: list[str] = []

        async def delayed_listing(request: httpx.Request) -> httpx.Response:
            response = peer.respond(request)
            if request.url.path.endswith("/messages"):
                if snapshot == "empty":
                    response = httpx.Response(
                        200,
                        json={
                            "data": [],
                            "metadata": {"has_more": False, "limit": 100},
                        },
                    )
                entered.set()
                await release.wait()
            return response

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        async with rest_client_over(delayed_listing) as rest:
            link.rest = rest
            recovery = asyncio.create_task(self.recover(ctx, phase))
            try:
                async with asyncio.timeout(1):
                    await entered.wait()
                    if mode is ControlMode.PLAY:
                        await ctx.resume_room()
                    elif mode is ControlMode.STOP:
                        ctx.stop_room()
                    else:
                        ctx.interrupt(kind=mode)
                    release.set()
                    result = await recovery
                assert result is (
                    False if phase == "bootstrap" else ResyncOutcome.BLOCKED
                )
                assert seen == []
                assert peer.transitions == []
            finally:
                release.set()
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

        async with rest_client_over(peer.respond) as rest:
            link.rest = rest
            if ctx.is_stopped:
                await ctx.resume_room()
            assert await self.recover(ctx, "idle") is ResyncOutcome.WORK
        assert seen == ["older", "stale"]
        assert peer.statuses == {
            "exhausted": "failed",
            "older": "processed",
            "stale": "processed",
        }

    async def test_server_order_survives_missing_timestamp(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("older", "pending")
        peer.add("newer", "pending")
        peer.messages["older"]["inserted_at"] = None
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")

        assert await self.recover(ctx, "bootstrap") is True
        assert seen == ["older", "newer"]

    async def test_delivery_status_change_cannot_hide_older_work(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("older", "processing")
        peer.add("newer", "pending")
        seen: list[str] = []

        def changing_listing(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/messages") and "page" in request.url.params:
                peer.statuses["older"] = "failed"
            return peer.respond(request)

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        async with rest_client_over(changing_listing) as rest:
            link.rest = rest
            assert await self.recover(ctx, "bootstrap") is True
        assert seen == ["older", "newer"]
        assert peer.statuses == {
            "exhausted": "failed",
            "older": "processed",
            "newer": "processed",
        }
        assert all("status" not in query for query in peer.listing_queries)

    async def test_completed_bootstrap_does_not_disable_later_synchronization(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        link, peer = backlog
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        assert await self.recover(ctx, "bootstrap") is True
        peer.add("missed-during-disconnect", "processing")
        assert await self.recover(ctx, "bootstrap") is True
        assert seen == ["missed-during-disconnect"]
        assert peer.statuses["missed-during-disconnect"] == "processed"
        assert peer.listing_queries == []

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("pagination", ["page", "cursor", "retired-page"])
    async def test_exhausted_head_preserves_failure_and_recovers_paginated_fifo(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, pagination: str
    ) -> None:
        link, peer = backlog
        peer.pagination = "mixed" if pagination == "retired-page" else pagination
        peer.reject_page_parameters = pagination == "retired-page"
        peer.add("exhausted", "failed")
        peer.add("old-pending", "pending")
        peer.add("retryable-failed", "failed")
        peer.add("stale-processing", "processing")
        peer.add("new-pending", "pending")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")

        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == [
            "old-pending",
            "retryable-failed",
            "stale-processing",
            "new-pending",
        ]
        assert peer.statuses["exhausted"] == "failed"
        assert all(mid != "exhausted" for mid, _ in peer.transitions)
        assert ctx._retry_tracker.is_permanently_failed("exhausted")
        assert not ctx.claims.is_completed(ctx.room_id, "exhausted")
        assert all("status" not in query for query in peer.listing_queries)
        assert all(query["sort_order"] == "asc" for query in peer.listing_queries)

        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.BLOCKED
        )
        await ctx._process_event(
            make_message_event(room_id=ROOM_ID, msg_id="new-pending")
        )
        assert len(seen) == 4
        assert peer.next_calls == 2

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    async def test_head_exhausted_during_recovery_does_not_hide_later_message(
        self, backlog: tuple[BandLink, BacklogApi], phase: str
    ) -> None:
        link, peer = backlog
        peer.add("failing-head", "pending")
        peer.add("later", "pending")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)
            if event.payload.id == "failing-head":
                raise RuntimeError("permanent handler failure")

        ctx = self.context(link, handle)

        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == ["failing-head", "failing-head", "later"]
        assert peer.statuses == {"failing-head": "failed", "later": "processed"}
        assert ctx._retry_tracker.is_permanently_failed("failing-head")
        assert ("failing-head", "processed") not in peer.transitions

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("skip", ["completed", "self-authored", "server-processed"])
    async def test_locally_skipped_repeated_head_never_reexecutes(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, skip: str
    ) -> None:
        link, peer = backlog
        peer.add(
            "skipped",
            "processed" if skip == "server-processed" else "pending",
            self_authored=skip == "self-authored",
        )
        peer.add("later", "pending")
        peer.fixed_head = "skipped"
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        if skip == "completed":
            ctx.claims.remember_completed(ctx.room_id, "skipped")

        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == ["later"]
        assert all(mid != "skipped" for mid, _ in peer.transitions)
        assert peer.next_calls == 1

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize(
        "skip", ["completed", "self-authored", "exhausted", "server-processed"]
    )
    async def test_skipped_only_head_finishes_bounded_pass(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, skip: str
    ) -> None:
        link, peer = backlog
        peer.add(
            "skipped",
            "processed" if skip == "server-processed" else "pending",
            self_authored=skip == "self-authored",
        )
        peer.fixed_head = "skipped"
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        if skip == "completed":
            ctx.claims.remember_completed(ctx.room_id, "skipped")
        elif skip == "exhausted":
            ctx._retry_tracker.mark_permanently_failed("skipped")

        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.BLOCKED
        )
        assert seen == []
        assert peer.transitions == []
        assert peer.next_calls == 1
        assert peer.list_calls == 1

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    async def test_inflight_eligible_snapshot_head_blocks_newer_work(
        self, backlog: tuple[BandLink, BacklogApi], phase: str
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("owned", "pending")
        peer.add("newer", "pending")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        assert ctx.claims.try_claim(ctx.room_id, "owned")
        try:
            assert await self.recover(ctx, phase) is (
                False if phase == "bootstrap" else ResyncOutcome.RETRY
            )
            assert seen == []
            assert peer.transitions == []
            assert ctx._sync_complete is False
        finally:
            ctx.claims.release(ctx.room_id, "owned")

        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == ["owned", "newer"]

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("failed_page", [1, 2])
    async def test_incomplete_snapshot_never_executes_partial_rows(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, failed_page: int
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("older", "pending")
        peer.add("newer", "pending")
        peer.listing_error = ("", failed_page)
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")

        assert await self.recover(ctx, phase) is (
            False if phase == "bootstrap" else ResyncOutcome.RETRY
        )
        assert seen == []
        assert peer.transitions == []
        assert peer.statuses["exhausted"] == "failed"

        peer.listing_error = None
        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == ["older", "newer"]

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("cursor_behavior", ["missing", "repeated"])
    async def test_unusable_cursor_continuation_is_not_drained(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, cursor_behavior: str
    ) -> None:
        link, peer = backlog
        peer.pagination = "cursor"
        peer.cursor_behavior = cursor_behavior
        peer.add("exhausted", "failed")
        peer.add("older", "pending")
        peer.add("newer", "pending")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")

        assert await self.recover(ctx, phase) is (
            False if phase == "bootstrap" else ResyncOutcome.RETRY
        )
        assert ctx._sync_complete is False
        assert peer.transitions == []
        assert seen == []
        assert peer.list_calls <= 2

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    async def test_ack_pending_snapshot_head_never_replays_or_overtakes(
        self, backlog: tuple[BandLink, BacklogApi], phase: str
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("unacked", "pending")
        peer.add("newer", "pending")
        peer.refuse_processed.add("unacked")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        ctx.claims.remember_ack_pending(ctx.room_id, "unacked")

        assert await self.recover(ctx, phase) is (
            False if phase == "bootstrap" else ResyncOutcome.RETRY
        )
        assert ctx.claims.is_ack_pending(ctx.room_id, "unacked")
        assert seen == []
        assert peer.transitions == []

        peer.refuse_processed.clear()
        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == ["newer"]
        assert peer.statuses["unacked"] == "processed"
        assert ("unacked", "processing") not in peer.transitions

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    async def test_retryable_snapshot_failure_blocks_newer_until_exhausted(
        self, backlog: tuple[BandLink, BacklogApi], phase: str
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("retryable", "failed")
        peer.add("newer", "pending")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)
            if event.payload.id == "retryable":
                raise RuntimeError("retryable failure")

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")

        for _ in range(2):
            assert await self.recover(ctx, phase) is (
                False if phase == "bootstrap" else ResyncOutcome.RETRY
            )
            assert peer.statuses["newer"] == "pending"
        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == ["retryable", "retryable", "newer"]
        assert peer.statuses["retryable"] == "failed"
        assert ctx._retry_tracker.is_permanently_failed("retryable")

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    async def test_unaccepted_snapshot_head_retains_fifo_and_failure_budget(
        self, backlog: tuple[BandLink, BacklogApi], phase: str
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("unaccepted", "pending")
        peer.add("newer", "pending")
        accepted = False
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            if not accepted:
                raise InputNotAccepted("provider is busy")
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        for _ in range(3):
            assert await self.recover(ctx, phase) is (
                False if phase == "bootstrap" else ResyncOutcome.BLOCKED
            )
        assert seen == []
        assert peer.statuses["newer"] == "pending"
        assert not ctx._retry_tracker.is_permanently_failed("unaccepted")
        assert ctx._deferred_message_id == "unaccepted"
        assert all(action != "failed" for _, action in peer.transitions)

        accepted = True
        assert await self.recover(ctx, phase) is (
            True if phase == "bootstrap" else ResyncOutcome.WORK
        )
        assert seen == ["unaccepted", "newer"]
        assert ctx._deferred_message_id is None

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("refusal", ["stopped", "empty-success", "unsuccessful"])
    async def test_snapshot_requires_accepted_claim_before_provider_effects(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, refusal: str
    ) -> None:
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("candidate", "pending")
        peer.add("newer", "pending")
        if refusal == "stopped":
            peer.processing_response = httpx.Response(204)
        elif refusal == "empty-success":
            peer.processing_response = httpx.Response(200)
        else:
            peer.processing_response = httpx.Response(
                200,
                json={
                    "data": {
                        "id": "candidate",
                        "status": "processing",
                        "attempt_number": 1,
                        "success": False,
                    }
                },
            )
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        await self.recover(ctx, phase)
        assert seen == []
        assert peer.statuses["newer"] == "pending"
        assert all(action != "processed" for _, action in peer.transitions)

        peer.processing_response = None
        if ctx.is_stopped:
            await ctx.resume_room()
        await self.recover(ctx, phase)
        assert seen == ["candidate", "newer"]

    async def test_overlapping_pages_are_deduplicated_before_any_claim(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        link, peer = backlog
        peer.pagination = "cursor"
        peer.add("exhausted", "failed")
        peer.add("older", "pending")
        peer.add("newer", "pending")
        seen: list[str] = []

        def overlapping_page(request: httpx.Request) -> httpx.Response:
            response = peer.respond(request)
            if request.url.path.endswith("/messages"):
                assert peer.transitions == []
                if request.url.params.get("cursor") == "3":
                    payload = response.json()
                    payload["data"].insert(0, peer.message("older"))
                    return httpx.Response(200, json=payload)
            return response

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        async with rest_client_over(overlapping_page) as rest:
            link.rest = rest
            assert await self.recover(ctx, "bootstrap") is True
        assert seen == ["older", "newer"]
        assert peer.transitions == [
            ("older", "processing"),
            ("older", "processed"),
            ("newer", "processing"),
            ("newer", "processed"),
        ]

    @pytest.mark.parametrize("skip", ["completed", "exhausted", "server-processed"])
    async def test_skipped_websocket_marker_reads_snapshot_before_queue_handoff(
        self, backlog: tuple[BandLink, BacklogApi], skip: str
    ) -> None:
        link, peer = backlog
        peer.add("marker", "processed" if skip == "server-processed" else "pending")
        peer.add("later", "pending")
        peer.fixed_head = "marker"
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        if skip == "completed":
            ctx.claims.remember_completed(ctx.room_id, "marker")
        elif skip == "exhausted":
            ctx._retry_tracker.mark_permanently_failed("marker")
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="marker"))
        await ctx.on_event(
            make_participant_added_event(room_id=ROOM_ID, participant_id="new-user")
        )
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="later"))
        assert await self.recover(ctx, "bootstrap") is True
        assert peer.list_calls == (1 if skip == "server-processed" else 2)
        assert seen == []
        assert peer.transitions == []
        while not ctx.queue.empty():
            await ctx._process_event(ctx.queue.get_nowait())
        assert seen == ["new-user", "later"]

    async def test_retryable_websocket_marker_is_not_removed_as_terminal(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        link, peer = backlog
        peer.add("marker", "pending")
        failed = True
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            if failed:
                raise RuntimeError("transient failure")
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        await ctx.on_event(make_message_event(room_id=ROOM_ID, msg_id="marker"))
        assert await self.recover(ctx, "bootstrap") is False
        assert ctx._first_ws_msg_id == "marker"
        assert ctx.queue.qsize() == 1
        assert ctx._sync_complete is False
        assert ctx._deferred_message_id == "marker"
        failed = False
        assert await self.recover(ctx, "bootstrap") is True
        assert seen == ["marker"]
        assert ctx.queue.empty()
        assert ctx._first_ws_msg_id is None

    async def test_terminal_deferred_head_preserves_synthetic_fifo_before_snapshot_rows(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        link, peer = backlog
        peer.add("unaccepted", "pending")
        peer.add("later", "pending")
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            if event.payload.id == "unaccepted":
                raise InputNotAccepted("provider is busy")
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        assert await self.recover(ctx, "idle") is ResyncOutcome.BLOCKED
        await ctx._retain_or_handle_control(
            make_message_event(
                room_id=ROOM_ID,
                msg_id="local-contact",
                sender_id="contact-events",
                sender_type="System",
            )
        )
        peer.statuses["unaccepted"] = "processed"
        peer.fixed_head = "unaccepted"
        assert await self.recover(ctx, "idle") is ResyncOutcome.BLOCKED
        assert peer.list_calls == 1
        assert seen == []
        assert peer.statuses["later"] == "pending"
        assert ctx._deferred_message_id is None
        assert await self.recover(ctx, "idle") is ResyncOutcome.WORK
        assert seen == ["local-contact", "later"]
        assert peer.statuses["later"] == "processed"

    async def test_bootstrap_snapshot_completes_before_queued_synthetic_work(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        """A synthetic turn queued while bootstrap is deferred must not stall the
        snapshot: bootstrap finishes the backlog, then the queue drains."""
        link, peer = backlog
        peer.add("first", "pending")
        peer.add("exhausted", "failed")
        peer.add("later", "pending")
        accepted = False
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            if not accepted:
                raise InputNotAccepted("provider is busy")
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        assert await self.recover(ctx, "bootstrap") is False
        await ctx._retain_or_handle_control(
            make_message_event(
                room_id=ROOM_ID,
                msg_id="local-contact",
                sender_id="contact-events",
                sender_type="System",
            )
        )
        accepted = True
        for _ in range(3):
            async with asyncio.timeout(1):
                await ctx._reconcile()

        assert seen == ["first", "later", "local-contact"]
        assert ctx._sync_complete
        assert not ctx._pending_synthetic
        assert peer.statuses["later"] == "processed"

    async def test_deferred_row_behind_terminal_head_settles_before_synthetic_work(
        self, backlog: tuple[BandLink, BacklogApi]
    ) -> None:
        """With an older real message deferred behind a terminal /next head, a
        queued synthetic turn waits for that message, then runs before later rows."""
        link, peer = backlog
        peer.add("exhausted", "failed")
        peer.add("deferred", "pending")
        peer.add("later", "pending")
        accepted = False
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            if not accepted:
                raise InputNotAccepted("provider is busy")
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        assert await self.recover(ctx, "idle") is ResyncOutcome.BLOCKED
        assert ctx._deferred_message_id == "deferred"
        await ctx._retain_or_handle_control(
            make_message_event(
                room_id=ROOM_ID,
                msg_id="local-contact",
                sender_id="contact-events",
                sender_type="System",
            )
        )
        accepted = True
        for _ in range(3):
            await self.recover(ctx, "idle")

        assert seen == ["deferred", "local-contact", "later"]
        assert ctx._deferred_message_id is None
        assert peer.statuses["later"] == "processed"

    @staticmethod
    async def retain_synthetic(ctx: ExecutionContext, *message_ids: str) -> None:
        for message_id in message_ids:
            await ctx._retain_or_handle_control(
                make_message_event(
                    room_id=ROOM_ID,
                    msg_id=message_id,
                    sender_id="contact-events",
                    sender_type="System",
                )
            )

    @staticmethod
    async def control_while_first_settles(
        ctx: ExecutionContext,
        peer: BacklogApi,
        control: ControlMode,
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        """Land ``control`` while the deferred row ``first`` is mid-turn, then
        run reconciliation until the room has had every chance to drain."""
        recovering = asyncio.create_task(ctx._reconcile())
        try:
            async with asyncio.timeout(2):
                await entered.wait()
                if control is ControlMode.STOP:
                    peer.stopped = True
                    ctx.stop_room()
                elif control is ControlMode.PLAY:
                    await ctx.resume_room()
                else:
                    ctx.interrupt(kind=control)
                release.set()
                await recovering
            if control is ControlMode.STOP:
                peer.stopped = False
                await ctx.resume_room()
            async with asyncio.timeout(2):
                for _ in range(4):
                    await ctx._reconcile()
        finally:
            release.set()
            recovering.cancel()
            await asyncio.gather(recovering, return_exceptions=True)

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("control", list(ControlMode))
    async def test_control_while_deferred_row_settles_never_strands_synthetic_work(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, control: ControlMode
    ) -> None:
        """A control landing while a deferred row finishes must still release that
        row's deferral, though a terminal head keeps /next from ever going empty;
        otherwise queued synthetic turns wait on a row that is already settled."""
        link, peer = backlog
        if phase == "bootstrap":
            peer.add("exhausted", "failed")
        peer.add("first", "pending")
        if phase == "idle":
            peer.add("exhausted", "failed")
        peer.add("later", "pending")
        entered, release = asyncio.Event(), asyncio.Event()
        accepted, gated = False, False
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            nonlocal gated
            if not accepted:
                raise InputNotAccepted("provider is busy")
            if event.payload.id == "first" and not gated:
                gated = True
                entered.set()
                await release.wait()
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        if phase == "idle":
            ctx._sync_complete = True
        assert await self.recover(ctx, phase) in {False, ResyncOutcome.BLOCKED}
        assert ctx._deferred_message_id == "first"
        await self.retain_synthetic(ctx, "local-A", "local-B")
        accepted = True
        try:
            await self.control_while_first_settles(ctx, peer, control, entered, release)
        finally:
            await ctx.stop()

        replayed = [] if control is ControlMode.INTERRUPT else ["first"]
        if phase == "bootstrap":
            # Bootstrap finishes the backlog before the queue, as /next sync does.
            assert seen == [*replayed, "later", "local-A", "local-B"]
        else:
            assert seen == [*replayed, "local-A", "local-B", "later"]
        assert ctx._deferred_message_id is None
        assert not ctx._pending_synthetic

    @pytest.mark.parametrize("phase", ["bootstrap", "idle"])
    @pytest.mark.parametrize("head", ["next", "terminal"])
    async def test_deferral_held_while_ack_pending_and_released_once_acked(
        self, backlog: tuple[BandLink, BacklogApi], phase: str, head: str
    ) -> None:
        """A deferred row whose processed ack fails keeps its deferral, so nothing
        overtakes it; once the ack retry lands, queued synthetic turns run next."""
        link, peer = backlog
        if head == "terminal":
            peer.add("exhausted", "failed")
        peer.add("first", "pending")
        peer.add("later", "pending")
        accepted = False
        seen: list[str] = []

        async def handle(ctx: ExecutionContext, event: Any) -> None:
            if not accepted:
                raise InputNotAccepted("provider is busy")
            seen.append(event.payload.id)

        ctx = self.context(link, handle)
        ctx._retry_tracker.mark_permanently_failed("exhausted")
        if phase == "idle":
            ctx._sync_complete = True
        try:
            async with asyncio.timeout(3):
                await self.recover(ctx, phase)
                assert ctx._deferred_message_id == "first"
                await self.retain_synthetic(ctx, "local-A", "local-B")
                accepted = True
                peer.refuse_processed.add("first")
                await ctx._reconcile()
                assert ctx.claims.is_ack_pending(ROOM_ID, "first")
                assert ctx._deferred_message_id == "first"
                assert seen == ["first"]
                assert peer.statuses["later"] == "pending"

                peer.refuse_processed.clear()
                for _ in range(4):
                    await ctx._reconcile()
        finally:
            await ctx.stop()

        if phase == "bootstrap":
            assert seen == ["first", "later", "local-A", "local-B"]
        else:
            assert seen == ["first", "local-A", "local-B", "later"]
        assert ctx._deferred_message_id is None
        assert not ctx._pending_synthetic
