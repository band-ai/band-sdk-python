"""MessageLifecycleMarks behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.link import BandLink


class TestMessageLifecycleMarks:
    """Tests for message lifecycle status return values."""

    @pytest.mark.asyncio
    async def test_mark_processing_returns_true_on_success(
        self, accepted_lifecycle_response: MagicMock
    ):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_processing = (
            AsyncMock(return_value=accepted_lifecycle_response)
        )

        result = await link.mark_processing("room-1", "msg-1")

        assert result is True
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_processing.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_processing_returns_false_on_error(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_processing = (
            AsyncMock(side_effect=Exception("network down"))
        )

        result = await link.mark_processing("room-1", "msg-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_processed_returns_true_on_success(
        self, accepted_lifecycle_response: MagicMock
    ):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_processed = (
            AsyncMock(return_value=accepted_lifecycle_response)
        )

        result = await link.mark_processed("room-1", "msg-1")

        assert result is True
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_processed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_processed_returns_false_on_error(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_processed = (
            AsyncMock(side_effect=Exception("network down"))
        )

        result = await link.mark_processed("room-1", "msg-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_failed_returns_true_on_success(
        self, accepted_lifecycle_response: MagicMock
    ):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed = (
            AsyncMock(return_value=accepted_lifecycle_response)
        )

        result = await link.mark_failed("room-1", "msg-1", "boom")

        assert result is True
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_failed_returns_false_on_error(self):
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.with_raw_response.mark_agent_message_failed = (
            AsyncMock(side_effect=Exception("network down"))
        )

        result = await link.mark_failed("room-1", "msg-1", "boom")

        assert result is False
