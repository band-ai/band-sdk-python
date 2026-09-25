from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk._errors import CLIConnectionError

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.helpers import error_events


class TestCLIConnectionError:
    """Tests for dead subprocess recovery via CLIConnectionError."""

    @pytest.mark.asyncio
    async def test_invalidates_session_on_cli_connection_error(
        self, sample_message, mock_tools
    ):
        """CLIConnectionError should invalidate the dead session and re-raise."""

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock(
            side_effect=CLIConnectionError("Cannot write to terminated process")
        )
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            # Dead session should be invalidated
            mock_manager.invalidate_session.assert_awaited_once_with("room-123")
            # Cached session ID should be cleared
            assert "room-123" not in adapter._session_ids

    @pytest.mark.asyncio
    async def test_cli_connection_error_reports_error_event(
        self, sample_message, mock_tools
    ):
        """CLIConnectionError should report error event to the user."""

        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=CLIConnectionError("Process dead"))
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            # Error should be surfaced to the user
            mock_tools.send_failure.assert_called_once()
            failure = mock_tools.send_failure.call_args.args[0]
            assert failure.provider == "claude_sdk"
            assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE

    @pytest.mark.asyncio
    async def test_clears_session_id_on_cli_connection_error(
        self, sample_message, mock_tools
    ):
        """CLIConnectionError should clear cached session ID so resume is not attempted."""

        adapter = ClaudeSDKAdapter()
        # Pre-populate a session ID
        adapter._session_ids["room-123"] = "sess-old"

        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=CLIConnectionError("Dead"))
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=False,
                    room_id="room-123",
                )

            assert "room-123" not in adapter._session_ids

    @pytest.mark.asyncio
    async def test_stream_ending_without_result_invalidates_session_and_fails_turn(
        self, sample_message, mock_tools
    ):
        """An EOF before ResultMessage is a dead client, not a successful turn."""
        adapter = ClaudeSDKAdapter()
        adapter._session_ids["room-123"] = "sess-old"
        mock_client = MagicMock()

        async def receive():
            if False:
                yield None

        mock_client.query = AsyncMock()
        mock_client.receive_response = MagicMock(return_value=receive())
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        mock_manager.invalidate_session = AsyncMock()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(CLIConnectionError, match="ended without a result"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=False,
                    room_id="room-123",
                )

        mock_manager.invalidate_session.assert_awaited_once_with("room-123")
        assert "room-123" not in adapter._session_ids
        errors = error_events(mock_tools)
        assert len(errors) == 1
        assert errors[0] == GENERIC_PROVIDER_FAILURE_MESSAGE
