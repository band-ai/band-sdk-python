"""Fixtures shared by the ExecutionContext interrupt/stop tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.message_lifecycle import ActionablePage
from tests.conftest import mirror_settlement_results
from tests.runtime.helpers import AGENT_ID


@pytest.fixture
def mock_link() -> MagicMock:
    link = MagicMock()
    link.agent_id = AGENT_ID
    link.rest = MagicMock()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.mark_processed = AsyncMock(return_value=True)
    link.mark_failed = AsyncMock(return_value=True)
    mirror_settlement_results(link)
    link.get_next_message = AsyncMock(return_value=None)
    link.get_actionable_page = AsyncMock(return_value=ActionablePage([], None))
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.report_activity = AsyncMock(return_value=True)
    return link
