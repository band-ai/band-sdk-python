from __future__ import annotations

from band.runtime.execution import (
    ExecutionContext,
    ExecutionState,
)


class TestExecutionContextConstruction:
    """Test ExecutionContext initialization."""

    def test_init_stores_room_id(self, mock_link, mock_handler):
        """Should store room_id."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.room_id == "room-123"
        assert ctx.thread_id == "room-123"  # Alias

    def test_init_starts_idle(self, mock_link, mock_handler):
        """Should start in starting state, not running."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.state is ExecutionState.STARTING
        assert ctx.is_running is False
        assert ctx.is_processing is False

    def test_init_empty_participants(self, mock_link, mock_handler):
        """Should start with empty participants."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.participants == []

    def test_init_llm_not_initialized(self, mock_link, mock_handler):
        """Should start with LLM not initialized."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert ctx.is_llm_initialized is False
