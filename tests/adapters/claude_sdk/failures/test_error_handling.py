from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE


class TestErrorHandling:
    """Tests for error handling when SDK or tools raise."""

    @pytest.mark.asyncio
    async def test_reports_error_on_query_failure(self, sample_message, mock_tools):
        """When client.query raises, adapter reports error via send_failure and re-raises."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=Exception("API Error"))
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(Exception, match="API Error"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

            mock_tools.send_failure.assert_called_once()
            failure = mock_tools.send_failure.call_args.args[0]
            assert failure.provider == "claude_sdk"
            assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
