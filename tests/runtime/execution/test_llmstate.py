"""ExecutionContextLLMState behavior."""

from __future__ import annotations

from band.runtime.execution import (
    ExecutionContext,
)


class TestExecutionContextLLMState:
    """Test LLM initialization state."""

    def test_mark_llm_initialized(self, mock_link, mock_handler):
        """mark_llm_initialized() should set flag."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        ctx.mark_llm_initialized()

        assert ctx.is_llm_initialized is True
