"""Tests for AgentRuntime._on_reconnected()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from band.runtime.runtime import AgentRuntime


class TestAgentRuntimeOnReconnected:
    """Tests for AgentRuntime._on_reconnected()."""

    async def test_calls_request_resync_on_all_executions(
        self, mock_link, mock_handler
    ):
        """_on_reconnected() should call request_resync() on each execution."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = MagicMock()
        exec1.request_resync = AsyncMock()
        exec2 = MagicMock()
        exec2.request_resync = AsyncMock()

        runtime.executions.update({"room-1": exec1, "room-2": exec2})

        await runtime._on_reconnected()

        exec1.request_resync.assert_called_once()
        exec2.request_resync.assert_called_once()

    async def test_skips_execution_without_request_resync(
        self, mock_link, mock_handler
    ):
        """_on_reconnected() should not raise if an execution lacks request_resync."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Simulate a legacy/custom Execution without the new method
        legacy_exec = MagicMock(spec=[])  # spec=[] → no attributes at all

        runtime.executions.update({"room-legacy": legacy_exec})

        # Should not raise AttributeError
        await runtime._on_reconnected()

    async def test_one_failure_does_not_abort_others(self, mock_link, mock_handler):
        """A failing request_resync() should not prevent the others from running."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = MagicMock()
        exec1.request_resync = AsyncMock(side_effect=RuntimeError("boom"))
        exec2 = MagicMock()
        exec2.request_resync = AsyncMock()

        runtime.executions.update({"room-1": exec1, "room-2": exec2})

        await runtime._on_reconnected()

        exec2.request_resync.assert_called_once()

    async def test_presence_reconnect_still_resyncs_active_executions_if_api_fails(
        self, mock_link, mock_handler
    ):
        """The full presence -> runtime callback chain should survive API failure."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        execution = MagicMock()
        execution.request_resync = AsyncMock()
        runtime.executions.update({"room-1": execution})

        mock_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            side_effect=RuntimeError("network error")
        )

        await runtime.presence._handle_reconnect()

        execution.request_resync.assert_called_once()
