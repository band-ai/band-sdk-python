from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import (
    SEND_MESSAGE_MCP_NAME,
    register_pending_approval,
)


class TestPendingApprovalEviction:
    """Tests for LRU eviction of pending approvals."""

    @pytest.mark.asyncio
    async def test_evicts_oldest_when_capacity_reached(self, mock_tools):
        """Should evict oldest pending when max capacity is reached."""

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            max_pending_approvals_per_room=1,
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="decline",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        # Pre-populate one pending approval
        old_future = register_pending_approval(
            adapter,
            tool_name="Old",
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )

        # Now trigger a new approval (should evict old one)
        callback = adapter._make_can_use_tool("room-1")
        await callback("New", {}, ToolPermissionContext())

        # Old future should have been evicted and declined
        assert old_future.done()
        assert old_future.result() is None

    @pytest.mark.asyncio
    async def test_evicted_approval_is_not_recorded_as_notified(self, mock_tools):
        """Eviction force-resolves the oldest pending approval, but never
        posts a room-visible notice for that specific call — only the
        original 'Approval requested' prompt, sent when it was first created.
        If the evicted call were recorded as notified and it happened to be
        the reply tool, the turn would end completely silent: no reply (the
        tool was declined) and no error (the guard wrongly suppressed)."""
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            max_pending_approvals_per_room=1,
            approval_wait_timeout_s=0.1,
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}
        callback = adapter._make_can_use_tool("room-1")

        async def request_first():
            return await callback(
                SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id="tool-1")
            )

        first_task = asyncio.create_task(request_first())
        await asyncio.sleep(0.02)  # let the first approval register + prompt

        second_result = await callback(
            "Bash", {"command": "ls"}, ToolPermissionContext(tool_use_id="tool-2")
        )
        first_result = await first_task

        assert isinstance(first_result, PermissionResultDeny)
        assert isinstance(second_result, PermissionResultDeny)
        assert "tool-1" not in adapter._notified_declines.get("room-1", set())
