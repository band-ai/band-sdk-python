from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import register_pending_approval


class TestApprovalCleanup:
    """Tests for approval cleanup on room/adapter cleanup."""

    @pytest.mark.asyncio
    async def test_on_cleanup_declines_pending_approvals(self):
        """Pending approvals should be declined when room is cleaned up."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        adapter._session_manager = AsyncMock()

        future = register_pending_approval(adapter)

        await adapter.on_cleanup("room-1")

        assert future.done()
        assert future.result() is None
        assert "room-1" not in adapter._pending_approvals

    @pytest.mark.asyncio
    async def test_cleanup_all_declines_all_rooms(self):
        """cleanup_all() should decline all pending approvals across rooms."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        adapter._session_manager = AsyncMock()

        f1 = register_pending_approval(adapter, room_id="room-1", tool_name="Bash")
        f2 = register_pending_approval(
            adapter, room_id="room-2", token="a-2", tool_name="Edit"
        )

        await adapter.cleanup_all()

        assert f1.result() is None
        assert f2.result() is None
        assert len(adapter._pending_approvals) == 0
