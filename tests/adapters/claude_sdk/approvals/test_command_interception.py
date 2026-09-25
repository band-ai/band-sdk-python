from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ApprovalReply, ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.core.types import PlatformMessage
from tests.adapters.claude_sdk.helpers import register_pending_approval


class TestOnMessageCommandInterception:
    """Tests for command interception in on_message()."""

    @pytest.mark.asyncio
    async def test_approve_command_intercepted(self, mock_tools):
        """Messages with /approve should not be sent to Claude."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")

        # Pre-populate a pending approval
        future = register_pending_approval(adapter)

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/approve a-1",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        mock_manager = AsyncMock()
        adapter._session_manager = mock_manager

        await adapter.on_message(
            msg=msg,
            tools=mock_tools,
            history=ClaudeSDKSessionState(text=""),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        # Should not have called get_or_create_session (no query sent)
        mock_manager.get_or_create_session.assert_not_awaited()
        # Future should be resolved
        assert future.result() == ApprovalReply("accept", "user-1")

    @pytest.mark.asyncio
    async def test_status_command_intercepted(self, mock_tools):
        """Messages with /status should be handled locally."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        mock_manager = MagicMock()
        mock_manager.get_session_count.return_value = 2
        mock_manager.get_or_create_session = AsyncMock()
        adapter._session_manager = mock_manager

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/status",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        await adapter.on_message(
            msg=msg,
            tools=mock_tools,
            history=ClaudeSDKSessionState(text=""),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        mock_manager.get_or_create_session.assert_not_awaited()
        mock_tools.send_message.assert_awaited_once()
        status_msg = mock_tools.send_message.call_args[0][0]
        assert "Claude SDK Status" in status_msg
        assert "manual" in status_msg

    @pytest.mark.asyncio
    async def test_approve_not_intercepted_when_approval_disabled(self, mock_tools):
        """Approval commands should be forwarded to Claude when approval_mode is None."""
        adapter = ClaudeSDKAdapter()  # approval_mode=None
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/approve a-1",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=msg,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

            # Should have queried Claude (not intercepted)
            mock_client.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_not_intercepted_when_approval_disabled(self, mock_tools):
        """/status should be forwarded to Claude when approval_mode is None."""
        adapter = ClaudeSDKAdapter()  # approval_mode=None
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        msg = PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="/status",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        )

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=msg,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

            # Should have queried Claude (not intercepted)
            mock_client.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_normal_message_not_intercepted(self, sample_message, mock_tools):
        """Normal messages should proceed to Claude query as usual."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # Should proceed to query Claude
            mock_client.query.assert_awaited_once()
