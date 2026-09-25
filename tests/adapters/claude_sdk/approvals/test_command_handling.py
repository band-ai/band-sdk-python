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
from tests.adapters.claude_sdk.helpers import (
    SEND_MESSAGE_MCP_NAME,
    register_pending_approval,
    reply_to_approval,
    wait_for_pending_approval,
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
            command="approvals",
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
            command="approvals",
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
            command="approve",
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
            command="decline",
            args="a-1",
            sender=sender,
        )
        assert future.done()
        assert future.result() == ApprovalReply("decline", sender["id"])

    @pytest.mark.asyncio
    async def test_approve_resolution_notice_failure_still_accepts(
        self, mock_tools, sender
    ):
        """An approve's confirmation notice is best-effort: a failed send must
        not turn an approved tool call into a decline."""
        mock_tools.send_message = AsyncMock(
            side_effect=[{"status": "sent"}, RuntimeError("network down")]
        )
        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=5)
        adapter._room_tools["room-1"] = mock_tools

        decision = await reply_to_approval(adapter, mock_tools, "approve", sender)

        assert isinstance(decision, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_a_reply_cancelled_mid_handling_still_resolves_the_approval(
        self, mock_tools, sender
    ):
        """Cancelling the room loop while it handles a reply must not strand
        the approval it claimed: the waiting tool call still gets the answer
        rather than hanging past its own deadline."""
        release_resolved_notice = asyncio.Event()

        async def _send_message(
            content: str, mentions: object = None
        ) -> dict[str, str]:
            if "resolved" in content:
                await release_resolved_notice.wait()
            return {"status": "sent"}

        mock_tools.send_message = AsyncMock(side_effect=_send_message)
        adapter = ClaudeSDKAdapter(approval_mode="manual", approval_wait_timeout_s=5)
        adapter._room_tools["room-1"] = mock_tools
        pending_task = asyncio.create_task(
            adapter._make_can_use_tool("room-1")(
                SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext()
            )
        )
        await wait_for_pending_approval(adapter)

        command_task = asyncio.create_task(
            adapter._handle_approval_command(
                tools=mock_tools,
                room_id="room-1",
                command="approve",
                args="a-1",
                sender=sender,
            )
        )
        await asyncio.sleep(0.01)
        command_task.cancel()
        release_resolved_notice.set()

        decision = await asyncio.wait_for(pending_task, timeout=1)
        assert isinstance(decision, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_approve_single_pending_no_token(
        self, adapter_with_approval, mock_tools, sender
    ):
        """When only 1 pending, /approve without token should resolve it."""
        future = register_pending_approval(adapter_with_approval)
        await adapter_with_approval._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
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
            command="approve",
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
            command="approve",
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
            command="approve",
            args="a-1",
            sender=sender,
        )

        msg = mock_tools.send_message.call_args[0][0]
        assert msg == "Approval `a-1` is no longer pending."
        assert not future.done()

    @pytest.mark.asyncio
    async def test_a_late_reply_during_the_timeout_notice_is_not_reported_as_resolved(
        self, mock_tools
    ) -> None:
        """A reply landing while the timeout notice is still being sent must
        not be told "resolved" for a decision that already timed out."""
        sending_second_message = asyncio.Event()
        release_second_message = asyncio.Event()
        call_count = 0

        async def _send_message(
            content: str, mentions: object = None
        ) -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                sending_second_message.set()
                await release_second_message.wait()
            return {"status": "sent"}

        mock_tools.send_message = AsyncMock(side_effect=_send_message)

        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.01,
            approval_timeout_decision="decline",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}
        callback = adapter._make_can_use_tool("room-1")

        pending_task = asyncio.create_task(
            callback(SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext())
        )
        await sending_second_message.wait()

        await adapter._handle_approval_command(
            tools=mock_tools,
            room_id="room-1",
            command="approve",
            args="a-1",
            sender={"id": "u1", "name": "Bob"},
        )
        reply = mock_tools.send_message.call_args_list[-1].args[0]
        release_second_message.set()

        decision = await pending_task
        assert isinstance(decision, PermissionResultDeny)
        assert reply == "Unknown approval token `a-1`. Available: none."

    @pytest.mark.asyncio
    async def test_a_reply_whose_notice_outlasts_the_deadline_still_wins(
        self, mock_tools
    ) -> None:
        """A reply claims the token, then its "resolved" notice is slow; the
        wait deadline passing meanwhile must not override the human's
        accept."""
        sending_resolved_notice = asyncio.Event()
        release_resolved_notice = asyncio.Event()

        async def _send_message(
            content: str, mentions: object = None
        ) -> dict[str, str]:
            if "resolved" in content:
                sending_resolved_notice.set()
                await release_resolved_notice.wait()
            return {"status": "sent"}

        mock_tools.send_message = AsyncMock(side_effect=_send_message)
        adapter = ClaudeSDKAdapter(
            approval_mode="manual",
            approval_wait_timeout_s=0.05,
            approval_timeout_decision="decline",
        )
        adapter._room_tools["room-1"] = mock_tools
        adapter._room_last_sender["room-1"] = {"id": "u1", "name": "Bob"}
        callback = adapter._make_can_use_tool("room-1")
        pending_task = asyncio.create_task(
            callback(SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext())
        )
        await asyncio.sleep(0)

        command_task = asyncio.create_task(
            adapter._handle_approval_command(
                tools=mock_tools,
                room_id="room-1",
                command="approve",
                args="a-1",
                sender={"id": "u1", "name": "Bob"},
            )
        )
        await sending_resolved_notice.wait()
        await asyncio.sleep(0.1)
        release_resolved_notice.set()
        await command_task

        assert isinstance(await pending_task, PermissionResultAllow)
