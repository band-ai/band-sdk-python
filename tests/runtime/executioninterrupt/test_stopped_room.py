"""Claims, acknowledgements and posts refused while the room is stopped."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.client.streaming import ControlMode
from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.cycle import TurnScope
from band.runtime.execution import BacklogProcessResult, ExecutionContext
from band.runtime.tools.agent import AgentTools
from band.runtime.types import SessionConfig
from tests.conftest import BlockingHandler, make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)
from tests.runtime.helpers import (
    LifecyclePlatform,
    rest_client_over,
)


@pytest.mark.parametrize("path", ["live", "backlog"])
async def test_stopped_claim_defers_without_invoking_or_poisoning_retry(
    mock_link, path: str
) -> None:
    handler = BlockingHandler(block=False)
    ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
    mock_link.mark_processing.side_effect = RoomExecutionStoppedError(ctx.room_id)
    if path == "live":
        assert not await ctx._process_event(make_message_event(msg_id="refused"))
    else:
        assert (
            await ctx._process_backlog_message(backlog_message("refused"))
            == BacklogProcessResult.RETRY_LATER
        )
    assert handler.invoked == []
    mock_link.mark_processed.assert_not_awaited()
    mock_link.mark_failed.assert_not_awaited()
    mock_link.mark_processing.side_effect = None
    assert (
        await ctx._process_backlog_message(backlog_message("refused"))
        == BacklogProcessResult.ADVANCED
    )
    assert handler.completed == ["refused"]


async def test_stopped_ack_retries_only_ack_after_play_beyond_budget(mock_link) -> None:
    handler = BlockingHandler(block=False)
    ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
    mock_link.mark_processed.side_effect = RoomExecutionStoppedError(ctx.room_id)
    assert not await ctx._process_event(make_message_event(msg_id="ack"))
    for _ in range(ctx.config.max_message_retries + 3):
        assert (
            await ctx._process_backlog_message(backlog_message("ack"))
            == BacklogProcessResult.RETRY_LATER
        )
        assert ctx.claims.is_ack_pending(ctx.room_id, "ack")
        assert not ctx.claims.is_completed(ctx.room_id, "ack")
    mock_link.mark_failed.assert_not_awaited()
    mock_link.mark_processed.side_effect = None
    assert (
        await ctx._process_backlog_message(backlog_message("ack"))
        == BacklogProcessResult.ADVANCED
    )
    assert handler.completed == ["ack"]
    assert ctx.claims.is_completed(ctx.room_id, "ack")


@pytest.mark.parametrize("post", ["message", "event"])
@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_real_stopped_post_aborts_scope_and_keeps_replayable(
    mock_link, post: str, suppress_cancel: bool
) -> None:
    peer = LifecyclePlatform()
    async with rest_client_over(peer.answer) as rest:
        mock_link.rest = rest
        invoked: list[str] = []

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            invoked.append(event.payload.id)
            tools = AgentTools.from_context(ctx)
            if len(invoked) == 1:
                try:
                    if post == "message":
                        await tools.send_message(
                            "answer", mentions=[{"id": "user-1", "handle": "user"}]
                        )
                    else:
                        await tools.execute_tool_call(
                            "band_send_event",
                            {"content": "thought", "message_type": "thought"},
                        )
                except asyncio.CancelledError:
                    if not suppress_cancel:
                        raise

        ctx = ExecutionContext("room-123", mock_link, handler, agent_id="agent-123")
        assert not await ctx._process_event(make_message_event(msg_id="stopped"))
        assert not ctx.is_stopped
        assert not ctx.claims.is_completed(ctx.room_id, "stopped")
        mock_link.mark_processed.assert_not_awaited()
        mock_link.mark_failed.assert_not_awaited()
        assert peer.posts == ["messages" if post == "message" else "events"]
        assert (
            await ctx._process_backlog_message(backlog_message("stopped"))
            == BacklogProcessResult.ADVANCED
        )
        assert invoked == ["stopped", "stopped"]


async def test_old_post_cannot_cancel_new_claim_window(mock_link) -> None:
    captured: list[AgentTools] = []
    invoked: list[str] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        captured.append(AgentTools.from_context(ctx))
        invoked.append(event.payload.id)

    ctx = ExecutionContext("room-123", mock_link, handler)
    await ctx._process_event(make_message_event(msg_id="old"))
    claiming, release = asyncio.Event(), asyncio.Event()

    async def claim(*_: Any) -> bool:
        claiming.set()
        await release.wait()
        return True

    mock_link.mark_processing.side_effect = claim
    mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
        side_effect=RoomExecutionStoppedError(ctx.room_id)
    )
    task = asyncio.create_task(ctx._process_event(make_message_event(msg_id="new")))
    try:
        await claiming.wait()
        with pytest.raises(asyncio.CancelledError):
            await captured[0].send_event("late", "thought")
        release.set()
        assert await task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert invoked == ["old", "new"]


async def test_delayed_stop_after_play_does_not_notify_old_control(mock_link) -> None:
    observer = AsyncMock()
    captured: list[AgentTools] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        captured.append(AgentTools.from_context(ctx))

    ctx = ExecutionContext("room-123", mock_link, handler, on_platform_stop=observer)
    await ctx._process_event(make_message_event(msg_id="old"))
    await ctx.resume_room()
    mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
        side_effect=RoomExecutionStoppedError(ctx.room_id)
    )
    with pytest.raises(asyncio.CancelledError):
        await captured[0].send_event("delayed", "thought")
    observer.assert_not_awaited()
    assert not ctx.is_stopped
    assert await ctx._process_event(make_message_event(msg_id="new"))


async def test_detached_stop_hook_does_not_await_its_posting_task(mock_link) -> None:
    captured: list[AgentTools] = []

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        captured.append(AgentTools.from_context(ctx))

    provider: asyncio.Task[None] | None = None
    cleanup = asyncio.Event()

    async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
        assert provider is not None
        provider.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await provider
        cleanup.set()

    ctx = ExecutionContext("room-123", mock_link, handler, on_platform_stop=observer)
    await ctx._process_event(make_message_event(msg_id="detached"))
    mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
        side_effect=RoomExecutionStoppedError(ctx.room_id)
    )
    provider = asyncio.create_task(captured[0].send_event("late", "thought"))
    with pytest.raises(asyncio.CancelledError):
        await provider
    await cleanup.wait()
    assert await ctx._process_event(make_message_event(msg_id="after"))


async def test_pending_stop_cleanup_defers_claims_with_bounded_wait(
    mock_link, monkeypatch
) -> None:
    release, entered = asyncio.Event(), asyncio.Event()

    async def observer(ctx: ExecutionContext, scope: TurnScope) -> None:
        entered.set()
        await release.wait()

    ctx = ExecutionContext(
        "room-123", mock_link, AsyncMock(), on_platform_stop=observer
    )
    await ctx._begin_scope()
    ctx.observe_platform_stop(ctx.current_scope)
    await entered.wait()
    monkeypatch.setattr("band.runtime.execution.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
    assert not await ctx._process_event(make_message_event(msg_id="later"))
    mock_link.mark_processing.assert_not_awaited()
    release.set()
    assert await ctx._process_event(make_message_event(msg_id="later"))


@pytest.mark.parametrize("refusal", ["mark", "post"])
async def test_stopped_failure_reporting_preserves_room_loop(
    mock_link, refusal: str
) -> None:
    first = True

    async def handler(ctx: ExecutionContext, event: Any) -> None:
        nonlocal first
        if first:
            first = False
            raise RuntimeError("provider failed")

    ctx = ExecutionContext("room-123", mock_link, handler)
    if refusal == "mark":
        mock_link.mark_failed.side_effect = RoomExecutionStoppedError(ctx.room_id)
    else:
        mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
            side_effect=RoomExecutionStoppedError(ctx.room_id)
        )
    assert await ctx._process_event(make_message_event(msg_id="failure"))
    assert await ctx._process_event(make_message_event(msg_id="next"))
    mock_link.mark_processed.assert_awaited_once_with("room-123", "next")


@pytest.mark.parametrize("suppress_cancel", [False, True])
@pytest.mark.parametrize("max_cycle_seconds", [None, 1.0])
async def test_newer_interrupt_consumes_a_scope_aborted_by_rest_stop(
    mock_link, suppress_cancel: bool, max_cycle_seconds: float | None
) -> None:
    cleanup_started = asyncio.Event()
    invoked: list[str] = []
    peer = LifecyclePlatform(stopped=True)
    async with rest_client_over(peer.answer) as rest:
        mock_link.rest = rest

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            invoked.append(event.payload.id)
            try:
                await AgentTools.from_context(ctx).send_event("thought", "thought")
            except asyncio.CancelledError:
                cleanup_started.set()
                try:
                    await asyncio.Future[None]()
                except asyncio.CancelledError:
                    if not suppress_cancel:
                        raise

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            config=SessionConfig(max_cycle_seconds=max_cycle_seconds),
        )
        async with asyncio.TaskGroup() as tasks:
            processing = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="interrupted"))
            )
            await cleanup_started.wait()
            peer.stopped = False
            await ctx.resume_room()
            assert ctx.interrupt(kind=ControlMode.INTERRUPT)
            await processing

        assert ctx.claims.is_completed(ctx.room_id, "interrupted")
        mock_link.mark_processed.assert_awaited_once_with(ctx.room_id, "interrupted")

        mock_link.mark_failed.assert_not_awaited()
        await ctx._process_backlog_message(backlog_message("interrupted"))
        assert invoked == ["interrupted"]


async def test_late_rest_refusal_preserves_a_pending_explicit_interrupt(
    mock_link,
) -> None:
    entered = asyncio.Event()
    peer = LifecyclePlatform(stopped=True)
    async with rest_client_over(peer.answer) as rest:
        mock_link.rest = rest

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            tools = AgentTools.from_context(ctx)
            entered.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                await tools.send_event("late", "thought")

        ctx = ExecutionContext("room-123", mock_link, handler)
        async with asyncio.TaskGroup() as tasks:
            processing = tasks.create_task(
                ctx._process_event(make_message_event(msg_id="interrupted"))
            )
            await entered.wait()
            assert ctx.interrupt(kind=ControlMode.INTERRUPT)
            await processing

        assert ctx.claims.is_completed(ctx.room_id, "interrupted")
        mock_link.mark_processed.assert_awaited_once_with(ctx.room_id, "interrupted")
