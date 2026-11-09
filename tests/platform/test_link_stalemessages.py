"""GetStaleProcessingMessages behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.platform.link import BandLink
from tests.platform.stale import make_stale_message


class TestGetStaleProcessingMessages:
    """Tests for stale processing recovery pagination."""

    @pytest.mark.asyncio
    async def test_paginates_across_all_pages(self):
        """get_stale_processing_messages should fetch every result page."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()

        msg_1 = make_stale_message(
            id="msg-1",
            room_id="room-1",
            content="first",
            sender_id="user-1",
            sender_name="User One",
        )
        msg_2 = make_stale_message(
            id="msg-2",
            room_id="room-1",
            content="second",
            sender_id="user-2",
            sender_name="User Two",
        )

        response_page_1 = MagicMock()
        response_page_1.data = [msg_1]
        response_page_1.metadata = MagicMock(page=1, total_pages=2)

        response_page_2 = MagicMock()
        response_page_2.data = [msg_2]
        response_page_2.metadata = MagicMock(page=2, total_pages=2)

        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            side_effect=[response_page_1, response_page_2]
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert [message.id for message in messages] == ["msg-1", "msg-2"]
        assert link.rest.agent_api_messages.list_agent_messages.await_count == 2
        first_call = link.rest.agent_api_messages.list_agent_messages.await_args_list[0]
        second_call = link.rest.agent_api_messages.list_agent_messages.await_args_list[
            1
        ]
        assert first_call.kwargs["page"] == 1
        assert second_call.kwargs["page"] == 2

    @pytest.mark.asyncio
    async def test_stops_after_first_page_when_total_pages_missing(self):
        """Missing pagination metadata should safely return the first page."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()

        msg = make_stale_message(
            id="msg-1",
            room_id="room-1",
            content="first",
            sender_id="user-1",
            sender_name="User One",
        )

        response_page_1 = MagicMock()
        response_page_1.data = [msg]
        response_page_1.metadata = MagicMock(total_pages=None)

        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            return_value=response_page_1
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert [message.id for message in messages] == ["msg-1"]
        link.rest.agent_api_messages.list_agent_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_failure(self):
        """This is a best-effort startup recovery sweep: a REST failure
        (mid-pagination or otherwise) must not crash agent startup — it
        returns an empty list instead of raising, unlike get_next_message's
        propagate-on-failure contract above."""
        link = BandLink(agent_id="agent-123", api_key="test-key")
        link.rest = MagicMock()
        link.rest.agent_api_messages.list_agent_messages = AsyncMock(
            side_effect=Exception("network down")
        )

        messages = await link.get_stale_processing_messages("room-1")

        assert messages == []
