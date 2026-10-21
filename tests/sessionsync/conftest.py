from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_link():
    """Create mock BandLink with all required async endpoints."""
    link = MagicMock()
    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.get_next_message = AsyncMock(return_value=None)
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.get_actionable_messages = AsyncMock(return_value=[])
    return link
