"""AgentRuntime execution context management."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime
from tests.runtime.agentruntime.kit import joined, left, settled
from tests.runtime.conftest import wait_for_condition


class TestAgentRuntimeExecutionManagement:
    """Test execution context management."""

    async def test_shutdown_rejects_new_joins_until_restart(
        self, mock_link, mock_handler
    ):
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        cleaned_rooms: list[str] = []

        async def cleanup(room_id: str) -> None:
            cleaned_rooms.append(room_id)
            cleanup_started.set()
            await finish_cleanup.wait()

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, on_session_cleanup=cleanup
        )
        await runtime.presence.on_room_joined("room-1", {})
        stopping = asyncio.create_task(runtime.stop())
        try:
            await cleanup_started.wait()
            await runtime.presence.on_room_joined("room-1", {})
            await runtime.presence.on_room_joined("room-2", {})
            assert runtime.active_sessions == {}
        finally:
            finish_cleanup.set()
            await stopping
        assert cleaned_rooms == ["room-1"]
        await runtime.presence.on_room_joined("room-2", {})
        assert runtime.active_sessions == {}
        await runtime.start()
        try:
            await runtime.presence.on_room_joined("room-2", {})
            await settled(runtime, "room-2")
            assert set(runtime.active_sessions) == {"room-2"}
        finally:
            await runtime.stop()

    async def test_creates_execution_on_room_joined(self, mock_link, mock_handler):
        """Room joined should create execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await joined(runtime, "room-123")

        assert "room-123" in runtime.executions
        assert isinstance(runtime.executions["room-123"], ExecutionContext)

        # Cleanup
        await runtime.stop()

    async def test_destroys_execution_on_room_left(self, mock_link, mock_handler):
        """Room left should destroy execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime.rooms.admit("room-123")
        await runtime._on_room_left("room-123")

        assert "room-123" not in runtime.executions

    async def test_execution_idempotent(self, mock_link, mock_handler):
        """Creating execution twice should return same instance."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = await runtime.rooms.admit("room-123")
        exec2 = await runtime.rooms.admit("room-123")

        assert exec1 is exec2

        await runtime.stop()

    async def test_all_contexts_share_one_claim_registry(self, mock_link, mock_handler):
        """Message ownership must be shared across every context the runtime
        creates — including a context recreated after room removal — while
        retaining room-local ownership state."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        room_a = await runtime.rooms.admit("room-a")
        room_b = await runtime.rooms.admit("room-b")
        await left(runtime, "room-a")
        room_a_again = await runtime.rooms.admit("room-a")

        assert room_a.claims is room_b.claims
        assert room_a_again.claims is room_a.claims

        await runtime.stop()

    async def test_pending_ack_is_retried_only_by_its_room(
        self, mock_link, mock_handler
    ):
        """A shared registry must not acknowledge a different room's message."""
        mock_link.mark_processed = AsyncMock(return_value=True)
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)
        room_a = await runtime.rooms.admit("room-a")
        room_b = await runtime.rooms.admit("room-b")
        room_a.claims.remember_ack_pending("room-a", "msg-1")

        assert await room_b._retry_pending_processed_acks()
        mock_link.mark_processed.assert_not_awaited()

        assert await room_a._retry_pending_processed_acks()
        mock_link.mark_processed.assert_awaited_once_with("room-a", "msg-1")

        await runtime.stop()

    async def test_room_recreation_reclaims_completed_and_retries_pending_ack(
        self, mock_link, mock_handler
    ):
        """Room teardown may drop durable cache, but not unacknowledged success."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)
        original = await runtime.rooms.admit("room-a")
        original.claims.remember_completed("room-a", "msg-completed")
        original.claims.remember_ack_pending("room-a", "msg-ack-pending")

        mock_link.mark_processed = AsyncMock(return_value=True)
        await left(runtime, "room-a")
        recreated = await runtime.rooms.admit("room-a")

        assert not recreated.claims.is_completed("room-a", "msg-completed")
        await wait_for_condition(lambda: mock_link.mark_processed.await_count == 1)
        mock_link.mark_processed.assert_awaited_once_with("room-a", "msg-ack-pending")

        await runtime.stop()

    async def test_active_sessions_returns_copy(self, mock_link, mock_handler):
        """active_sessions should return a copy."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime.rooms.admit("room-123")
        sessions = runtime.active_sessions

        # Modifying returned dict shouldn't affect internal state
        sessions["room-999"] = MagicMock()

        assert "room-999" not in runtime.executions

        await runtime.stop()
