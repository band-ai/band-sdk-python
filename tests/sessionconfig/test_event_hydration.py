from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.runtime.scheduler.driver import attempt_delivery


class TestProcessEventHydration:
    """Test _process_event() hydration behavior."""

    @pytest.mark.asyncio
    async def test_skips_hydration_when_disabled(
        self, mock_link: MagicMock, sample_event: MessageEvent
    ) -> None:
        """Should NOT call hydrate() when enable_context_hydration is False."""
        handler_called = False

        async def track_handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            nonlocal handler_called
            handler_called = True

        config = SessionConfig(enable_context_hydration=False)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=track_handler,
            config=config,
        )

        await attempt_delivery(ctx, sample_event)

        assert handler_called
        # Should NOT have fetched context
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_hydrates_context_when_enabled(
        self, mock_link: MagicMock, sample_event: MessageEvent
    ) -> None:
        """Should call hydrate() when enable_context_hydration is True."""
        handler_called = False

        async def track_handler(ctx: ExecutionContext, event: PlatformEvent) -> None:
            nonlocal handler_called
            handler_called = True

        config = SessionConfig(enable_context_hydration=True)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=track_handler,
            config=config,
        )

        await attempt_delivery(ctx, sample_event)

        assert handler_called
        # Should have fetched context
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_called_once()
