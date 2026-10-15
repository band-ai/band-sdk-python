"""ContextHydrationConfig behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

from band.runtime.execution import (
    ExecutionContext,
)
from band.runtime.sessionconfig import SessionConfig


class TestContextHydrationConfig:
    """Test context hydration behavior with config."""

    async def test_get_context_skips_history_api_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """get_context() should skip history but still load participants when hydration disabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        context = await ctx.get_context()

        # History should be empty (skipped), but participants are always loaded
        assert context.messages == []
        assert len(context.participants) == 1
        assert context.participants[0]["id"] == "user-1"
        assert context.participants[0]["name"] == "User One"
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_not_called()

    async def test_get_context_calls_api_when_hydration_enabled(
        self, mock_link, mock_handler
    ):
        """get_context() should call API when hydration enabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=True),
        )

        context = await ctx.get_context()

        # Should have called API
        mock_link.rest.agent_api_context.get_agent_chat_context.assert_called_once()
        assert len(context.messages) > 0

    async def test_get_history_for_llm_empty_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """get_history_for_llm() should return empty when hydration disabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        # Must hydrate first (but with disabled config, returns empty history)
        await ctx.get_context()
        history = ctx.get_history_for_llm()

        assert history == []

    async def test_hydrate_loads_participants_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """hydrate() should load participants even when context hydration is disabled."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.hydrate()

        # Participants should be loaded
        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "User One"
        mock_link.rest.agent_api_participants.list_agent_chat_participants.assert_called_once()

    async def test_build_participants_message_works_when_hydration_disabled(
        self, mock_link, mock_handler
    ):
        """build_participants_message() should work with participants loaded via hydrate()."""
        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

        await ctx.hydrate()
        msg = ctx.build_participants_message()

        # Should contain participant info
        assert "User One" in msg

    async def test_participants_preserved_when_history_hydration_fails(
        self, mock_link, mock_handler
    ):
        """Participants should be preserved even when history loading fails."""
        # Make history API fail
        mock_link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=Exception("API error")
        )

        ctx = ExecutionContext(
            "room-123",
            mock_link,
            mock_handler,
            config=SessionConfig(enable_context_hydration=True),
        )

        await ctx.hydrate()

        # Participants should still be loaded despite history failure
        assert len(ctx.participants) == 1
        assert ctx.participants[0]["name"] == "User One"
        # Context should have empty messages but populated participants
        context = ctx.build_context()
        assert context.messages == []
        assert len(context.participants) == 1
