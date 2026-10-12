"""Posts refused while the room is stopped."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.core.exceptions import RoomExecutionStoppedError
from band.runtime.cycle import TurnScope
from band.runtime.execution import BacklogProcessResult, ExecutionContext
from band.runtime.tools.agent import AgentTools
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import (
    backlog_message,
)
from tests.runtime.helpers import (
    LifecyclePlatform,
    rest_client_over,
)


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

    async def test_old_post_cannot_cancel_new_claim_window(self, mock_link) -> None:
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
        await ctx._process_event(make_message_event(msg_id="detached"))
        mock_link.rest.agent_api_events.create_agent_chat_event = AsyncMock(
            side_effect=RoomExecutionStoppedError(ctx.room_id)
        )
        provider = asyncio.create_task(captured[0].send_event("late", "thought"))
        with pytest.raises(asyncio.CancelledError):
            await provider
        await cleanup.wait()
        assert await ctx._process_event(make_message_event(msg_id="after"))
