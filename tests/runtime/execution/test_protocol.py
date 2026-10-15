"""ExecutionContextProtocol behavior."""

from __future__ import annotations

from band.runtime.execution import (
    Execution,
    ExecutionContext,
)


class TestExecutionContextProtocol:
    """Test that ExecutionContext implements Execution protocol."""

    def test_implements_execution_protocol(self, mock_link, mock_handler):
        """ExecutionContext should implement Execution protocol."""
        ctx = ExecutionContext("room-123", mock_link, mock_handler)

        assert isinstance(ctx, Execution)
