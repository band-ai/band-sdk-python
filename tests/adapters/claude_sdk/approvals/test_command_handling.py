from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
)

from band.adapters.claude_sdk import ApprovalReply, ClaudeSDKAdapter, ClaudeSDKCommand
from tests.adapters.claude_sdk.helpers import (
    ClaudeApprovalRoom,
    register_pending_approval,
)


class TestApprovalCommandHandling:
    """Tests for /approve, /decline, /approvals command handling."""

    @pytest.fixture
    def adapter_with_approval(self):
        return ClaudeSDKAdapter(approval_mode="manual")

    @pytest.fixture
    def sender(self):
        return {"id": "user-456", "name": "Alice"}

    @pytest.mark.asyncio
    async def test_approvals_empty(self, adapter_with_approval, mock_tools, sender):
        """Should report no pending approvals."""
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVALS,
            args="",
            sender=sender,
        )
        mock_tools.send_message.assert_awaited_once()
        assert "No pending" in mock_tools.send_message.call_args[0][0]

    @pytest.mark.asyncio
    async def test_approvals_lists_pending(
        self, adapter_with_approval, mock_tools, sender
    ):
        """Should list pending approvals with token, summary, and age."""
        register_pending_approval(
            adapter_with_approval, tool_input={"command": "ls"}, summary="Bash: `ls`"
        )
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVALS,
            args="",
            sender=sender,
        )
        msg = mock_tools.send_message.call_args[0][0]
        assert "a-1" in msg
        assert "Bash" in msg

    @pytest.mark.asyncio
    async def test_approve_resolves_future(
        self, adapter_with_approval, mock_tools, sender
    ):
        """Should resolve the pending future with 'accept'."""
        future = register_pending_approval(adapter_with_approval)
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVE,
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == ApprovalReply("accept", sender["id"])

    @pytest.mark.asyncio
    async def test_decline_resolves_future(
        self, adapter_with_approval, mock_tools, sender
    ):
        """Should resolve the pending future with 'decline'."""
        future = register_pending_approval(adapter_with_approval)
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.DECLINE,
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == ApprovalReply("decline", sender["id"])

    @pytest.mark.asyncio
    async def test_approve_single_pending_no_token(
        self, adapter_with_approval, mock_tools, sender
    ):
        """When only 1 pending, /approve without token should resolve it."""
        future = register_pending_approval(adapter_with_approval)
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVE,
            args="",
            sender=sender,
        )
        assert future.result() == ApprovalReply("accept", sender["id"])

    @pytest.mark.asyncio
    async def test_approve_multiple_pending_no_token(
        self, adapter_with_approval, mock_tools, sender
    ):
        """When multiple pending, /approve without token should ask for token."""
        register_pending_approval(adapter_with_approval, token="a-1", tool_name="Bash")
        register_pending_approval(adapter_with_approval, token="a-2", tool_name="Edit")
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVE,
            args="",
            sender=sender,
        )
        msg = mock_tools.send_message.call_args[0][0]
        assert "specify" in msg.lower()

    @pytest.mark.asyncio
    async def test_unknown_token(self, adapter_with_approval, mock_tools, sender):
        """Should report unknown token with available tokens."""
        register_pending_approval(adapter_with_approval)
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVE,
            args="bad-token",
            sender=sender,
        )
        msg = mock_tools.send_message.call_args[0][0]
        assert "Unknown" in msg
        assert "a-1" in msg

    @pytest.mark.asyncio
    async def test_a_reply_for_an_already_claimed_token_is_told_not_pending(
        self, adapter_with_approval, mock_tools, sender
    ) -> None:
        """A timeout that claimed the token first owns it: the command must
        not touch the future or say "resolved"."""
        future = register_pending_approval(adapter_with_approval)
        registry = adapter_with_approval._pending_approvals["room-1"]
        claimed = registry.try_claim("a-1")
        assert claimed is not None

        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVE,
            args="a-1",
            sender=sender,
        )

        msg = mock_tools.send_message.call_args[0][0]
        assert msg == "Approval `a-1` is no longer pending."
        assert not future.done()

    @pytest.mark.asyncio
    async def test_a_late_reply_during_the_timeout_notice_is_not_reported_as_resolved(
        self, approval_room: Callable[..., ClaudeApprovalRoom]
    ) -> None:
        """A reply landing while the timeout notice is still being sent must
        not be told "resolved" for a decision that already timed out."""
        room = approval_room(
            approval_wait_timeout_s=0.01, approval_timeout_decision="decline"
        )
        timeout_notice = room.tools.hold_message("timed out")
        decision = room.request()

        async with timeout_notice:
            await room.reply(ClaudeSDKCommand.APPROVE, "a-1")
            assert room.chat[-1] == "Unknown approval token `a-1`. Available: none."

        assert isinstance(await decision, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_an_approval_stands_however_its_resolved_notice_fares(
        self, approval_room: Callable[..., ClaudeApprovalRoom]
    ) -> None:
        """The reply claims the approval, then its "resolved" notice is slow
        enough to outlast the wait deadline and finally fails to send; neither
        may turn the human's accept into a decline."""
        room = approval_room(
            approval_wait_timeout_s=0.05, approval_timeout_decision="decline"
        )
        resolved_notice = room.tools.hold_message(
            "resolved", error=RuntimeError("network down")
        )
        decision = room.request()
        await room.until_pending()

        await room.reply(ClaudeSDKCommand.APPROVE, "a-1")
        async with resolved_notice:
            await asyncio.sleep(0.1)

        assert isinstance(await decision, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_a_reply_that_claims_while_the_prompt_send_fails_still_wins(
        self, approval_room: Callable[..., ClaudeApprovalRoom]
    ) -> None:
        """The approver answered while the prompt send was still failing: their
        answer owns the approval, so it is honored and confirmed, not
        overridden by the undelivered-prompt decline."""
        room = approval_room(approval_wait_timeout_s=5)
        failing_prompt = room.tools.hold_message(
            "Approval requested", error=RuntimeError("network down")
        )
        decision = room.request()

        async with failing_prompt:
            await room.reply(ClaudeSDKCommand.APPROVE)

        assert isinstance(await asyncio.wait_for(decision, 1), PermissionResultAllow)
        assert room.chat[-1] == "Approval `a-1` resolved as **accept**."

    @pytest.mark.asyncio
    async def test_bare_approve_ignores_an_approval_already_being_answered(
        self, adapter_with_approval, mock_tools, sender
    ) -> None:
        """Only open approvals count: with one claimed and one open, a bare
        /approve resolves the open one instead of asking for a token."""
        claimed = register_pending_approval(adapter_with_approval, token="a-1")
        open_future = register_pending_approval(adapter_with_approval, token="a-2")
        assert adapter_with_approval._pending_approvals["room-1"].try_claim("a-1")

        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command=ClaudeSDKCommand.APPROVE,
            args="",
            sender=sender,
        )

        assert open_future.result() == ApprovalReply("accept", sender["id"])
        assert not claimed.done()
