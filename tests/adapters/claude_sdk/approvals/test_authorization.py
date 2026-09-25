from __future__ import annotations

import pytest

from band.adapters.claude_sdk import ApprovalReply, ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import register_pending_approval


class TestApprovalAuthorization:
    """Tests for approval_authorized_senders access control."""

    @pytest.fixture
    def authorized_sender(self):
        return {"id": "admin-1", "name": "Admin"}

    @pytest.fixture
    def unauthorized_sender(self):
        return {"id": "user-99", "name": "Stranger"}

    @pytest.mark.asyncio
    async def test_authorized_sender_can_approve(self, mock_tools, authorized_sender):
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_authorized_senders={"admin-1"},
        )
        future = register_pending_approval(adapter)
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender=authorized_sender,
        )
        assert future.done()
        assert future.result() == ApprovalReply("accept", authorized_sender["id"])

    @pytest.mark.asyncio
    async def test_unauthorized_sender_rejected(self, mock_tools, unauthorized_sender):
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_authorized_senders={"admin-1"},
        )
        future = register_pending_approval(adapter)
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender=unauthorized_sender,
        )
        assert not future.done()  # Future should NOT be resolved
        msg = mock_tools.send_message.call_args[0][0]
        assert "not authorized" in msg.lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", ["", "a-9"], ids=["no-token", "unknown-token"])
    @pytest.mark.parametrize("pending_count", [0, 2])
    async def test_unauthorized_sender_is_refused_before_token_lookup(
        self, mock_tools, unauthorized_sender, args, pending_count
    ):
        """However the command names its approval, an unauthorized sender is
        refused rather than guided toward a token to retry with."""
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_authorized_senders={"admin-1"},
        )
        futures = [
            register_pending_approval(adapter, token=f"a-{n}")
            for n in range(1, pending_count + 1)
        ]
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="decline",
            args=args,
            sender=unauthorized_sender,
        )
        msg = mock_tools.send_message.call_args[0][0]
        assert msg == "You are not authorized to approve or decline tool use."
        assert not any(future.done() for future in futures)

    @pytest.mark.asyncio
    async def test_unauthorized_sender_can_list_approvals(
        self, mock_tools, unauthorized_sender
    ):
        """/approvals should be available to all participants regardless of auth."""
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_authorized_senders={"admin-1"},
        )
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approvals",
            args="",
            sender=unauthorized_sender,
        )
        assert "No pending" in mock_tools.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_restriction_when_authorized_senders_is_none(self, mock_tools):
        """When approval_authorized_senders is None, any sender can approve."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        sender = {"id": "anyone", "name": "Anyone"}
        future = register_pending_approval(adapter)
        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == ApprovalReply("accept", sender["id"])
