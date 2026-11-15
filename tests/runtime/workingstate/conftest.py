from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import mirror_settlement_results


@pytest.fixture
def mock_link() -> MagicMock:
    link = MagicMock()
    link.agent_id = "agent-123"
    link.rest = MagicMock()

    participant1 = MagicMock()
    participant1.id = "user-1"
    participant1.name = "User One"
    participant1.type = "User"
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[participant1])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )

    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.report_activity = AsyncMock(return_value=True)
    return mirror_settlement_results(link)


@pytest.fixture
def mock_handler() -> AsyncMock:
    return AsyncMock()
