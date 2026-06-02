"""
Tests for ExecutionContext asyncio shutdown pattern.

Tests cover:
- Instant cancellation via task.cancel() (no timeout waiting)
- is_running property reflects task state
- stop() returns immediately even when queue.get() is blocked
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig

# Import test helpers from conftest
from tests.conftest import make_message_event


class TestIsRunningProperty:
    """Tests for is_running property reflecting task state."""

    @pytest.fixture
    def mock_link(self):
        """Create mock BandLink."""
        link = MagicMock()
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.get_next_message = AsyncMock(return_value=None)
        link.mark_processing = AsyncMock()
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        return link

    @pytest.fixture
    def ctx(self, mock_link):
        """Create ExecutionContext with mocked dependencies."""

        async def handler(ctx, event):
            pass

        return ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=handler,
        )

    def test_is_running_false_before_start(self, ctx):
        """is_running should be False before start()."""
        assert ctx.is_running is False

    @pytest.mark.asyncio
    async def test_is_running_true_after_start(self, ctx):
        """is_running should be True after start()."""
        await ctx.start()
        try:
            assert ctx.is_running is True
        finally:
            await ctx.stop()

    @pytest.mark.asyncio
    async def test_is_running_false_after_stop(self, ctx):
        """is_running should be False after stop()."""
        await ctx.start()
        await ctx.stop()
        assert ctx.is_running is False


class TestInstantShutdown:
    """Tests for instant cancellation without timeout waiting."""

    @pytest.fixture
    def mock_link(self):
        """Create mock BandLink."""
        link = MagicMock()
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.get_next_message = AsyncMock(return_value=None)
        link.mark_processing = AsyncMock()
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        return link

    @pytest.fixture
    def ctx(self, mock_link):
        """Create ExecutionContext with mocked dependencies."""

        async def handler(ctx, event):
            pass

        return ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=handler,
        )

    @pytest.mark.asyncio
    async def test_stop_returns_quickly_when_idle(self, ctx):
        """stop() should return quickly even when waiting on empty queue."""
        await ctx.start()

        # Give loop time to reach queue.get()
        await asyncio.sleep(0.01)

        # Stop should be instant (no 60-second timeout)
        start = asyncio.get_running_loop().time()
        await ctx.stop()
        elapsed = asyncio.get_running_loop().time() - start

        # Should complete in well under 1 second
        assert elapsed < 0.5, f"stop() took {elapsed}s - should be instant"

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, ctx):
        """Multiple stop() calls should be safe."""
        await ctx.start()
        await ctx.stop()
        await ctx.stop()  # Should not raise
        await ctx.stop()  # Should not raise

        assert ctx.is_running is False

    @pytest.mark.asyncio
    async def test_stop_before_start_is_safe(self, ctx):
        """stop() without start() should be safe."""
        await ctx.stop()  # Should not raise
        assert ctx.is_running is False


class TestCancellationDuringSync:
    """Tests for cancellation during synchronization phase."""

    @pytest.fixture
    def mock_link_slow_sync(self):
        """Create mock BandLink with slow /next that can be cancelled."""
        link = MagicMock()
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )

        # Simulate slow /next API that can be cancelled
        async def slow_get_next(room_id):
            await asyncio.sleep(10)  # Would take 10 seconds if not cancelled
            return None

        link.get_next_message = slow_get_next
        link.mark_processing = AsyncMock()
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        return link

    @pytest.fixture
    def slow_sync_ctx(self, mock_link_slow_sync):
        """Create ExecutionContext with slow sync."""

        async def handler(ctx, event):
            pass

        return ExecutionContext(
            room_id="room-123",
            link=mock_link_slow_sync,
            on_execute=handler,
        )

    @pytest.mark.asyncio
    async def test_stop_cancels_sync_immediately(self, slow_sync_ctx):
        """stop() should cancel sync phase immediately."""
        await slow_sync_ctx.start()

        # Give time to enter sync
        await asyncio.sleep(0.01)

        # Stop should be instant despite slow sync
        start = asyncio.get_running_loop().time()
        await slow_sync_ctx.stop()
        elapsed = asyncio.get_running_loop().time() - start

        # Should complete in well under 1 second (not 10 seconds)
        assert elapsed < 0.5, f"stop() took {elapsed}s - sync should be cancelled"


class TestCancellationDuringProcessing:
    """Tests for cancellation during event processing."""

    @pytest.fixture
    def mock_link(self):
        """Create mock BandLink."""
        link = MagicMock()
        link.rest = MagicMock()
        link.rest.agent_api_participants = MagicMock()
        link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.rest.agent_api_context = MagicMock()
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=MagicMock(data=[])
        )
        link.get_next_message = AsyncMock(return_value=None)
        link.mark_processing = AsyncMock()
        link.mark_processed = AsyncMock()
        link.mark_failed = AsyncMock()
        return link

    @pytest.fixture
    def ctx_with_slow_handler(self, mock_link):
        """Create ExecutionContext with slow handler."""

        async def slow_handler(ctx, event):
            await asyncio.sleep(10)  # Would take 10 seconds

        return ExecutionContext(
            room_id="room-123",
            link=mock_link,
            on_execute=slow_handler,
            config=SessionConfig(enable_context_hydration=False),
        )

    @pytest.mark.asyncio
    async def test_stop_cancels_processing(self, ctx_with_slow_handler):
        """stop() should cancel event processing."""
        ctx = ctx_with_slow_handler
        await ctx.start()

        # Wait for sync to complete
        await asyncio.sleep(0.05)

        # Enqueue an event to trigger processing
        event = make_message_event(room_id="room-123", msg_id="msg-001")
        await ctx.on_event(event)

        # Give time to start processing
        await asyncio.sleep(0.05)

        # Stop should cancel processing
        start = asyncio.get_running_loop().time()
        await ctx.stop()
        elapsed = asyncio.get_running_loop().time() - start

        # Should complete quickly (not wait 10 seconds for handler)
        assert elapsed < 1.0, f"stop() took {elapsed}s - should cancel processing"
