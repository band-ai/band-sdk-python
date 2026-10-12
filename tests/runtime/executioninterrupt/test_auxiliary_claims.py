"""Auxiliary claims made from inside a running turn."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from band.client.rest import AsyncRestClient
from band.client.streaming import DeliveryStatus
from band.platform.link import BandLink
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.types import SessionConfig
from tests.conftest import make_message_event
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.control import AuxiliaryClaimRuntime
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    ClaimGate,
    LifecyclePlatform,
    rest_client_over,
)


def _auxiliary_context(
    rest: AsyncRestClient,
    handler: Callable[[ExecutionContext, Any], Awaitable[None]],
    *,
    observer: Callable[[ExecutionContext, TurnScope], Awaitable[None]] | None = None,
    max_cycle_seconds: float | None = None,
    execution_type: type[ExecutionContext] = ExecutionContext,
) -> ExecutionContext:
    link = BandLink(agent_id=AGENT_ID, api_key="test-key")
    link.rest = rest
    return execution_type(
        ROOM_ID,
        link,
        handler,
        agent_id=AGENT_ID,
        on_platform_stop=observer,
        config=SessionConfig(
            enable_context_hydration=False,
            enable_working_state=False,
            max_message_retries=1,
            max_cycle_seconds=max_cycle_seconds,
        ),
    )


@pytest.mark.parametrize("accepted", [True, False])
async def test_auxiliary_claim_returns_acceptance_without_ending_turn(
    accepted: bool,
) -> None:
    peer = LifecyclePlatform()
    results: list[bool] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if not accepted and request.url.path.endswith("/aux/processing"):
            return httpx.Response(503)
        return peer.answer(request)

    async with rest_client_over(answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            results.append(await ctx.claim_message("aux"))

        ctx = _auxiliary_context(rest, handler)
        await ctx._process_event(make_message_event(msg_id="trigger"))
        assert results == [accepted]
        assert peer.marked("processed") == ["trigger"]
        assert peer.requested("failed") == []


@pytest.mark.parametrize("path", ["live", "backlog"])
@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_auxiliary_refusal_unwinds_ownership_and_preserves_replay(
    path: str, suppress_cancel: bool
) -> None:
    peer = LifecyclePlatform()
    invoked: list[str] = []
    downstream: list[str] = []
    released: list[bool] = []
    async with rest_client_over(peer.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            invoked.append(event.payload.id)
            owned: list[str] = []
            try:
                for mid in ("aux-1", "aux-2"):
                    assert ctx.claims.try_claim(ctx.room_id, mid)
                    owned.append(mid)
                    if len(invoked) == 1 and mid == "aux-2":
                        peer.stopped = True
                    assert await ctx.claim_message(mid)
                downstream.append(event.payload.id)
            except asyncio.CancelledError:
                if not suppress_cancel:
                    raise
            finally:
                for mid in owned:
                    ctx.claims.release(ctx.room_id, mid)
                released.append(
                    not set(owned).intersection(ctx.claims.inflight_ids(ctx.room_id))
                )

        ctx = _auxiliary_context(rest, handler)
        if path == "live":
            await ctx._process_event(make_message_event(msg_id="trigger"))
        else:
            await ctx._process_backlog_message(backlog_message("trigger"))
        assert downstream == []
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []
        assert released == [True]
        peer.stopped = False
        await ctx.resume_room()
        await ctx._process_backlog_message(backlog_message("trigger"))
        assert downstream == ["trigger"]
        assert invoked == ["trigger", "trigger"]
        assert peer.marked("processed") == ["trigger"]
        assert released == [True, True]


@pytest.mark.parametrize("owner", ["outside", "subtask"])
async def test_auxiliary_claim_rejects_non_handler_tasks_before_rest(
    owner: str,
) -> None:
    peer = LifecyclePlatform()
    async with rest_client_over(peer.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            with pytest.raises(RuntimeError, match="active handler"):
                await asyncio.create_task(ctx.claim_message("aux"))

        ctx = _auxiliary_context(rest, handler)
        if owner == "outside":
            with pytest.raises(RuntimeError, match="active handler"):
                await ctx.claim_message("aux")
            assert peer.requested_marks == []
        else:
            await ctx._process_event(make_message_event(msg_id="trigger"))
            assert peer.requested_marks == [
                ("trigger", "processing"),
                ("trigger", "processed"),
            ]


async def test_auxiliary_observed_stop_prevents_further_claims() -> None:
    peer = LifecyclePlatform()
    async with rest_client_over(peer.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            peer.stopped = True
            with pytest.raises(asyncio.CancelledError):
                await ctx.claim_message("refused")
            with pytest.raises(asyncio.CancelledError):
                await ctx.claim_message("never-requested")

        ctx = _auxiliary_context(rest, handler)
        await ctx._process_event(make_message_event(msg_id="trigger"))
        assert peer.requested_marks == [
            ("trigger", "processing"),
            ("refused", "processing"),
        ]
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


@pytest.mark.parametrize("control", ["stop", "interrupt", "play", "stop-play"])
async def test_auxiliary_claim_checks_control_after_suppressed_transport_cancel(
    control: str,
) -> None:
    peer = LifecyclePlatform()
    gate = ClaimGate(peer, "aux", suppress_cancel=True)
    downstream: list[str] = []
    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            assert await ctx.claim_message("aux")
            downstream.append(event.payload.id)

        ctx = _auxiliary_context(rest, handler)
        async with asyncio.TaskGroup() as tasks:
            processing = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="trigger"))
            )
            await gate.entered.wait()
            match control:
                case "stop" | "stop-play":
                    ctx.stop_room()
                case "interrupt":
                    assert ctx.interrupt()
            if control in {"play", "stop-play"}:
                await ctx.resume_room()
            gate.release.set()
            await processing
        assert downstream == (["trigger"] if control == "play" else [])
        assert peer.marked("processed") == (
            ["trigger"] if control in {"play", "interrupt"} else []
        )
        assert peer.requested("failed") == []


@pytest.mark.parametrize("new_scope", [False, True])
@pytest.mark.parametrize("refused", [False, True])
async def test_detached_auxiliary_response_cannot_continue_or_notify_room(
    new_scope: bool, refused: bool
) -> None:
    peer = LifecyclePlatform()
    gate = ClaimGate(
        peer,
        "old-aux",
        response=httpx.Response(204) if refused else None,
        suppress_cancel=True,
    )
    old_finished, new_started, finish_new = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    downstream: list[str] = []
    notified: list[str] = []
    misuse_rejected: list[bool] = []

    async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
        notified.append(ctx.room_id)

    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            if event.payload.id == "old":
                try:
                    await ctx.claim_message("old-aux")
                    downstream.append("old")
                except asyncio.CancelledError:
                    with pytest.raises(RuntimeError, match="active handler"):
                        await ctx.claim_message("detached")
                    misuse_rejected.append(True)
                    raise
                finally:
                    old_finished.set()
            else:
                new_started.set()
                await finish_new.wait()
                downstream.append("new")

        ctx = _auxiliary_context(rest, handler, observer=observer, max_cycle_seconds=60)
        processing = asyncio.create_task(
            ctx._process_event(make_message_event(msg_id="old"))
        )
        await gate.entered.wait()
        processing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await processing
        await gate.cancelled.wait()
        async with asyncio.TaskGroup() as tasks:
            if new_scope:
                newer = tasks.create_task(
                    ctx._process_event(make_message_event(msg_id="new"))
                )
                await new_started.wait()
            gate.release.set()
            await old_finished.wait()
            finish_new.set()
            if new_scope:
                await newer
        assert downstream == (["new"] if new_scope else [])
        assert notified == []
        assert misuse_rejected == [True]
        assert peer.marked("processed") == (["new"] if new_scope else [])
        assert peer.requested("failed") == []


async def test_auxiliary_stop_during_accepted_claim_cannot_continue() -> None:
    peer = LifecyclePlatform()
    gate = ClaimGate(peer, "aux", suppress_cancel=True)
    captured: list[AgentTools] = []
    downstream: list[str] = []
    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            captured.append(AgentTools.from_context(ctx))
            await ctx.claim_message("aux")
            downstream.append(event.payload.id)

        ctx = _auxiliary_context(rest, handler)
        async with asyncio.TaskGroup() as tasks:
            processing = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="trigger"))
            )
            await gate.entered.wait()
            with pytest.raises(asyncio.CancelledError):
                await captured[0].send_event("late", "thought")
            gate.release.set()
            await processing
        assert downstream == []
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []


class ClosingExecution(ExecutionContext):
    waiting_for_idle: asyncio.Event

    async def _wait_for_idle(self, timeout: float) -> bool:
        self.waiting_for_idle.set()
        return await super()._wait_for_idle(timeout)


@pytest.mark.parametrize("graceful", [False, True])
async def test_auxiliary_claim_preserves_shutdown_priority_and_grace(
    graceful: bool,
) -> None:
    peer = LifecyclePlatform()
    peer.add_message("trigger")
    gate = ClaimGate(peer, "aux", suppress_cancel=True)
    downstream: list[str] = []
    async with rest_client_over(gate.answer) as rest:

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            await ctx.claim_message("aux")
            downstream.append(event.payload.id)

        ctx = _auxiliary_context(
            rest, handler, max_cycle_seconds=60, execution_type=ClosingExecution
        )
        assert isinstance(ctx, ClosingExecution)
        ctx.waiting_for_idle = asyncio.Event()
        await ctx.start()
        await gate.entered.wait()
        shutdown = asyncio.create_task(ctx.stop(timeout=60 if graceful else None))
        if graceful:
            await ctx.waiting_for_idle.wait()
        else:
            await gate.cancelled.wait()
        gate.release.set()
        assert await shutdown
        assert downstream == (["trigger"] if graceful else [])
        assert not ctx.is_running


async def test_auxiliary_control_runtime_releases_then_commits_replayed_burst() -> None:
    peer = LifecyclePlatform()
    control = AuxiliaryClaimRuntime()
    control.auxiliaries.put_nowait("aux-1")
    control.auxiliaries.put_nowait("aux-2")
    control.release_claim.set()
    refuse = True

    def answer(request: httpx.Request) -> httpx.Response:
        if refuse and request.url.path.endswith("/aux-2/processing"):
            peer.stopped = True
        return peer.answer(request)

    async with rest_client_over(answer) as rest:
        ctx = _auxiliary_context(rest, control.on_execute)
        await ctx._process_event(make_message_event(msg_id="trigger"))
        assert control.cancelled.is_set()
        assert not control.owned_ids
        assert not control.downstream_started.is_set()
        assert peer.requested("processed") == []
        assert peer.requested("failed") == []
        refuse = False
        peer.stopped = False
        await ctx.resume_room()
        await ctx._process_backlog_message(backlog_message("trigger"))
        assert control.downstream_started.is_set()
        assert control.completed_message_ids == ["trigger"]
        assert peer.marked("processed") == ["aux-1", "aux-2", "trigger"]
        assert not ctx.claims.pending_ack_ids(ctx.room_id)
        assert not control.owned_ids


async def test_live_capture_reads_durable_delivery_without_observer_frames() -> None:
    peer = LifecyclePlatform()
    peer.add_message("trigger")
    peer.messages[0]["metadata"] = {
        "delivery_status": {AGENT_ID: {"status": DeliveryStatus.PROCESSING}}
    }
    peer.add_message("unclaimed")

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": peer.messages, "metadata": {"has_more": False, "limit": 50}},
        )

    async with rest_client_over(answer) as rest:
        capture = ReplyCapture(ROOM_ID, user_ops=UserOps(rest))
        assert await capture.durable_delivery_statuses(
            ("trigger", "unclaimed"), AGENT_ID
        ) == {"trigger": DeliveryStatus.PROCESSING, "unclaimed": None}
        with pytest.raises(AssertionError, match="missing"):
            await capture.durable_delivery_statuses(("missing",), AGENT_ID)
