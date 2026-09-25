from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from band.adapters.claude_sdk import ApprovalReply, ClaudeSDKAdapter


class TestCanUseToolCallback:
    """Tests for the can_use_tool callback (auto and manual modes)."""

    @pytest.mark.asyncio
    async def test_auto_accept_returns_allow(self, mock_tools):
        """auto_accept mode should return PermissionResultAllow."""

        adapter = ClaudeSDKAdapter(approval_mode="auto_accept")
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_auto_accept_sends_notification(self, mock_tools):
        """auto_accept should send policy notification when enabled."""

        adapter = ClaudeSDKAdapter(
            approval_mode="auto_accept", approval_text_notifications=True
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        mock_tools.send_message.assert_awaited_once()
        msg = mock_tools.send_message.call_args[0][0]
        assert "accept" in msg.lower()

    @pytest.mark.asyncio
    async def test_auto_decline_returns_deny(self, mock_tools):
        """auto_decline mode should return PermissionResultDeny."""

        adapter = ClaudeSDKAdapter(approval_mode="auto_decline")
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_auto_accept_no_notification_when_disabled(self, mock_tools):
        """Should not send notification when approval_text_notifications=False."""

        adapter = ClaudeSDKAdapter(
            approval_mode="auto_accept", approval_text_notifications=False
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        mock_tools.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manual_mode_sends_approval_request(self, mock_tools):
        """Manual mode should send approval message and wait on future."""

        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=1.0)
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")

        # Simulate user approving shortly after request
        async def approve_soon():
            await asyncio.sleep(0.05)
            pending = adapter._pending_approvals.get("room-1", {})
            for item in pending.values():
                if not item.future.done():
                    item.future.set_result(ApprovalReply("accept", "u1"))

        asyncio.get_running_loop().create_task(approve_soon())

        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultAllow)
        # Should have sent an approval request message
        assert mock_tools.send_message.await_count >= 1

    @pytest.mark.asyncio
    async def test_manual_mode_timeout_declines(self, mock_tools):
        """Manual mode should decline on timeout when timeout_decision='decline'."""

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="decline",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_manual_mode_timeout_accepts(self, mock_tools):
        """Manual mode should accept on timeout when timeout_decision='accept'."""

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="accept",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_manual_mode_notification_failure_declines(self, mock_tools):
        """If the approval notification can't be delivered, decline immediately."""

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=5.0,
        )
        mock_tools.send_message = AsyncMock(side_effect=RuntimeError("network down"))
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}

        callback = adapter._make_can_use_tool("room-1")
        result = await callback("Bash", {"command": "ls"}, ToolPermissionContext())

        assert isinstance(result, PermissionResultDeny)
        # Should not leave a dangling pending approval
        assert len(adapter._pending_approvals.get("room-1", {})) == 0
