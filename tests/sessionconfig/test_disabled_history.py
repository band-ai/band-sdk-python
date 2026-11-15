from __future__ import annotations

from unittest.mock import MagicMock

from band.runtime.execution import ExecutionContext, ExecutionHandler
from band.runtime.sessionconfig import SessionConfig


class TestGetHistoryForLLMHydrationDisabled:
    """Test get_history_for_llm() when hydration is disabled."""

    def test_returns_empty_when_hydration_disabled(
        self, mock_link: MagicMock, dummy_handler: ExecutionHandler
    ) -> None:
        """Should return empty list when enable_context_hydration is False."""
        config = SessionConfig(enable_context_hydration=False)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=dummy_handler,
            config=config,
        )

        history = ctx.get_history_for_llm()

        assert history == []

    def test_returns_empty_with_exclude_id_when_hydration_disabled(
        self, mock_link: MagicMock, dummy_handler: ExecutionHandler
    ) -> None:
        """Should return empty list even with exclude_message_id parameter."""
        config = SessionConfig(enable_context_hydration=False)
        ctx = ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=dummy_handler,
            config=config,
        )

        history = ctx.get_history_for_llm(exclude_message_id="msg-456")

        assert history == []
