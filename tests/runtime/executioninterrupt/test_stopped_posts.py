"""Posts refused while the room is stopped."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.core.exceptions import RoomExecutionStoppedError
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS, bind_turn_task
from band.runtime.cycle import TurnScope
from band.runtime.delivery import TurnOutcome
from band.runtime.execution import ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import auxiliary_context, backlog_message
from tests.runtime.helpers import (
    LifecyclePlatform,
    ResponseGate,
    rest_client_over,
)
from tests.runtime.scheduler.driver import attempt_delivery


class TestStoppedPosts:
    """A refused post aborts only its own scope and keeps the message replayable."""

    @pytest.mark.parametrize("post", ["message", "event"])
    @pytest.mark.parametrize("suppress_cancel", [False, True])
    async def test_real_stopped_post_aborts_scope_and_keeps_replayable(
        self, mock_link, post: str, suppress_cancel: bool
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
            assert (
                await attempt_delivery(ctx, make_message_event(msg_id="stopped"))
                is TurnOutcome.RETRY_LATER
            )
            assert not ctx.is_stopped
            assert not ctx.claims.is_completed(ctx.room_id, "stopped")
            mock_link.mark_processed.assert_not_awaited()
            mock_link.mark_failed.assert_not_awaited()
            assert peer.posts == ["messages" if post == "message" else "events"]
            assert (
                await attempt_delivery(ctx, backlog_message("stopped"))
                is TurnOutcome.ADVANCED
            )
            assert invoked == ["stopped", "stopped"]

    async def test_old_post_cannot_cancel_new_claim_window(self) -> None:
        captured: list[AgentTools] = []
        invoked: list[str] = []

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            captured.append(AgentTools.from_context(ctx))
            invoked.append(event.payload.id)

        peer = LifecyclePlatform()
        peer.add_message("old")
        peer.add_message("new")
        gate = ResponseGate(peer, "/processing")
        gate.release.set()
        async with rest_client_over(gate.answer) as rest:
            ctx = auxiliary_context(rest, handler)
            await attempt_delivery(ctx, peer.event("old"))
            gate.entered.clear()
            gate.release.clear()
            task = asyncio.create_task(attempt_delivery(ctx, peer.event("new")))
            try:
                await gate.entered.wait()
                # The old turn's tools post into a room the platform now holds stopped.
                peer.stopped = True
                with pytest.raises(asyncio.CancelledError):
                    await captured[0].send_event("late", "thought")
                peer.stopped = False
                gate.release.set()
                assert await task is TurnOutcome.ADVANCED
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        assert invoked == ["old", "new"]
        assert peer.marked("processed") == ["old", "new"]

    async def test_delayed_stop_after_play_does_not_notify_old_control(
        self, mock_link
    ) -> None:
        observer = AsyncMock()
        captured: list[AgentTools] = []

        async def handler(ctx: ExecutionContext, event: Any) -> None:
            captured.append(AgentTools.from_context(ctx))

        ctx = ExecutionContext(
            "room-123", mock_link, handler, on_platform_stop=observer
        )
        await attempt_delivery(ctx, make_message_event(msg_id="old"))
        await ctx.resume_room()
        mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
            side_effect=RoomExecutionStoppedError(ctx.room_id)
        )
        with pytest.raises(asyncio.CancelledError):
            await captured[0].send_event("delayed", "thought")
        observer.assert_not_awaited()
        assert not ctx.is_stopped
        assert (
            await attempt_delivery(ctx, make_message_event(msg_id="new"))
            is TurnOutcome.ADVANCED
        )

    async def test_detached_stop_hook_does_not_await_its_posting_task(
        self, mock_link
    ) -> None:
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

        ctx = ExecutionContext(
            "room-123", mock_link, handler, on_platform_stop=observer
        )
        await attempt_delivery(ctx, make_message_event(msg_id="detached"))
        mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
            side_effect=RoomExecutionStoppedError(ctx.room_id)
        )
        provider = asyncio.create_task(captured[0].send_event("late", "thought"))
        with pytest.raises(asyncio.CancelledError):
            await provider
        await cleanup.wait()
        assert (
            await attempt_delivery(ctx, make_message_event(msg_id="after"))
            is TurnOutcome.ADVANCED
        )

    @pytest.mark.looptime
    async def test_stopped_post_cancels_a_released_worker_at_its_grace(
        self, mock_link
    ) -> None:
        peer = LifecyclePlatform()
        async with rest_client_over(peer.answer) as rest:
            mock_link.rest = rest
            ctx = ExecutionContext(
                "room-123", mock_link, AsyncMock(), agent_id="agent-123"
            )
            await ctx.begin_delivery()
            tools = AgentTools.from_context(ctx)
            refused = asyncio.Event()
            refused_at: list[float] = []

            async def released_worker() -> None:
                # Provider tool calls post from their own task, so the refusal
                # cannot unwind the worker itself.
                post = asyncio.create_task(
                    tools.send_message(
                        "late", mentions=[{"id": "user-1", "handle": "user"}]
                    )
                )
                await asyncio.wait({post})
                refused_at.append(asyncio.get_running_loop().time())
                refused.set()
                await asyncio.Future[None]()

            worker = bind_turn_task(tools, asyncio.create_task(released_worker()))
            await ctx.begin_delivery()
            await refused.wait()
            await asyncio.wait({worker.task}, timeout=5 * CYCLE_CANCEL_GRACE_SECONDS)

            assert peer.posts == ["messages"]
            assert worker.task.cancelled()
            assert (
                asyncio.get_running_loop().time() - refused_at[0]
                == CYCLE_CANCEL_GRACE_SECONDS
            )
