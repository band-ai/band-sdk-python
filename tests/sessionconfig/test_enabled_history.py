from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.runtime.execution import ExecutionContext, ExecutionHandler
from band.runtime.sessionconfig import SessionConfig


class TestGetHistoryForLLMHydrationEnabled:
    """Test get_history_for_llm() when hydration is enabled (default)."""

    @pytest.mark.asyncio
    async def test_fetches_and_returns_history_when_enabled(
        self, history_link: MagicMock, dummy_handler: ExecutionHandler
    ) -> None:
        """Should fetch and return history when enable_context_hydration is True."""
        config = SessionConfig(enable_context_hydration=True)
        ctx = ExecutionContext(
            room_id="room-123",
            link=history_link,
            on_execute=dummy_handler,
            config=config,
        )

        # Hydrate context first
        await ctx.hydrate()
        history = ctx.get_history_for_llm()

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Hi there!"
        # Should have called get_agent_chat_context
        history_link.rest.agent_api_context.get_agent_chat_context.assert_called_once()
