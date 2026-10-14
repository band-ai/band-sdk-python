"""Agent compositor: DefaultPreprocessorIntegration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.agent import Agent
from band.client.streaming import (
    ParticipantAddedPayload,
    RoomAddedPayload,
)
from band.platform.event import ParticipantAddedEvent, RoomAddedEvent


class TestDefaultPreprocessorIntegration:
    """Tests for DefaultPreprocessor behavior."""

    @pytest.mark.asyncio
    async def test_default_preprocessor_filters_non_message_events(
        self, mock_runtime, mock_adapter
    ):
        """DefaultPreprocessor should filter non-message events."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        # Create a non-MessageEvent (e.g., RoomAddedEvent)

        mock_ctx = MagicMock()
        mock_event = RoomAddedEvent(
            room_id="room-123",
            payload=RoomAddedPayload(
                id="room-123",
                title="Test Room",
                inserted_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            ),
        )

        await agent._on_execute(mock_ctx, mock_event)

        # Should not call adapter (event was filtered)
        mock_adapter.on_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_preprocessor_filters_participant_events(
        self, mock_runtime, mock_adapter
    ):
        """Participant events should not become adapter execution turns."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        mock_ctx = MagicMock()
        mock_event = ParticipantAddedEvent(
            room_id="room-123",
            payload=ParticipantAddedPayload(
                id="user-123",
                name="Test User",
                type="User",
            ),
        )

        await agent._on_execute(mock_ctx, mock_event)

        mock_adapter.on_event.assert_not_awaited()
