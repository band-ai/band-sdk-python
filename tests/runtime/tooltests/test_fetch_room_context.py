from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.tools import AgentTools


class TestFetchRoomContext:
    """Paging the agent context endpoint."""

    @pytest.mark.asyncio
    async def test_prefers_the_required_pagination_field(
        self, mock_rest_client: MagicMock
    ) -> None:
        """`metadata` is required; `meta` is optional and often absent.

        Reading only `meta` collapses every room to one synthesized page, so a
        caller paging through history silently stops at the first page.
        """
        pagination = MagicMock()
        pagination.model_dump.return_value = {"total_pages": 3}
        mock_rest_client.agent_api_context.get_agent_chat_context = AsyncMock(
            return_value=SimpleNamespace(data=[], metadata=pagination)
        )

        context = await AgentTools("room-123", mock_rest_client).fetch_room_context(
            room_id="room-123"
        )

        assert context["meta"]["total_pages"] == 3
