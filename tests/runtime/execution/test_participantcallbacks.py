from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.event import PlatformEvent
from band.runtime.execution import (
    ExecutionContext,
)
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import (
    make_participant_added_event,
    make_participant_removed_event,
)
from tests.runtime.conftest import wait_for_condition


class TestParticipantCallbacks:
    """Tests for participant callbacks in ExecutionContext."""

    async def test_participant_added_callback_runs_before_handler(
        self, mock_link, mock_handler
    ):
        """participant_added callback should see updated participant state."""
        on_participant_added = AsyncMock()

        async def handler(ctx, event):
            assert any(p["id"] == "user-2" for p in ctx.participants)
            await mock_handler(ctx, event)

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            config=SessionConfig(enable_context_hydration=False),
            on_participant_added=on_participant_added,
        )
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
        )
        await ctx.on_event(event)
        await wait_for_condition(lambda: mock_handler.await_count >= 1)

        on_participant_added.assert_awaited_once_with("room-123", event)
        mock_handler.assert_awaited_once()

        await ctx.stop()

    async def test_participant_removed_callback_runs_before_handler(
        self, mock_link, mock_handler
    ):
        """participant_removed callback should see updated participant state."""
        on_participant_removed = AsyncMock()

        async def handler(ctx, event):
            assert all(p["id"] != "user-1" for p in ctx.participants)
            await mock_handler(ctx, event)

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            handler,
            on_participant_removed=on_participant_removed,
        )
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        event = make_participant_removed_event(
            room_id="room-123",
            participant_id="user-1",
        )
        await ctx.on_event(event)
        await wait_for_condition(lambda: mock_handler.await_count >= 1)

        on_participant_removed.assert_awaited_once_with("room-123", event)
        mock_handler.assert_awaited_once()

        await ctx.stop()

    @pytest.mark.looptime
    @pytest.mark.parametrize("startup_delay", [0.0, 2.0])
    async def test_participant_callback_error_does_not_block_handler(
        self,
        mock_link: MagicMock,
        mock_handler: AsyncMock,
        startup_delay: float,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        completed = asyncio.Event()

        async def next_message(room_id: str) -> None:
            await asyncio.sleep(startup_delay)

        async def handler_completed(
            ctx: ExecutionContext, event: PlatformEvent
        ) -> None:
            completed.set()

        mock_link.get_next_message.side_effect = next_message
        mock_handler.side_effect = handler_completed
        on_participant_added = AsyncMock(side_effect=RuntimeError("callback failed"))
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
            on_participant_added=on_participant_added,
        )
        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
        )
        await ctx.start()
        try:
            await ctx.on_event(event)
            await completed.wait()
            on_participant_added.assert_awaited_once()
            mock_handler.assert_awaited_once_with(ctx, event)
            assert "on_participant_added error for room-123" in caplog.text
        finally:
            await ctx.stop()
