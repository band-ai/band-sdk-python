"""MarkFailed behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.link import BandLink


class TestMarkFailed:
    """Tests for mark_failed error normalization."""

    @pytest.mark.asyncio
    async def test_replaces_empty_error_with_unknown(
        self, accepted_lifecycle_response: MagicMock
    ):
        """mark_failed should replace empty error string with 'Unknown error'."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed = (
            AsyncMock(return_value=accepted_lifecycle_response)
        )

        await link.mark_failed("room-1", "msg-1", "")

        link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed.assert_called_once()
        call_kwargs = link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "Unknown error"

    @pytest.mark.asyncio
    async def test_replaces_whitespace_error_with_unknown(
        self, accepted_lifecycle_response: MagicMock
    ):
        """mark_failed should replace whitespace-only error with 'Unknown error'."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed = (
            AsyncMock(return_value=accepted_lifecycle_response)
        )

        await link.mark_failed("room-1", "msg-1", "   ")

        call_kwargs = link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "Unknown error"

    @pytest.mark.asyncio
    async def test_passes_through_non_empty_error(
        self, accepted_lifecycle_response: MagicMock
    ):
        """mark_failed should pass through a valid error string as-is."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed = (
            AsyncMock(return_value=accepted_lifecycle_response)
        )

        await link.mark_failed("room-1", "msg-1", "connection reset")

        call_kwargs = link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed.call_args
        assert call_kwargs.kwargs["error"] == "connection reset"
