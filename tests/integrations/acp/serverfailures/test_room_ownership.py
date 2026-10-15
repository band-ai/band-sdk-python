from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from acp.exceptions import RequestError

from band.core.types import MessageType
from tests.integrations.acp.conftest import (
    deliver_server_message,
    failure_event,
    make_platform_message,
    wait_for_pending_prompt,
)
from tests.integrations.acp.serverfailures.kit import (
    assert_default_room_failure,
    prompt,
    running_server,
)


class TestRoomOwnership:
    @pytest.mark.asyncio
    async def test_second_same_room_prompt_does_not_replace_first(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        first = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        with pytest.raises(RequestError) as raised:
            await server.prompt(session_id="session-1", prompt=[{"text": "Second"}])
        assert raised.value.code == -32602
        await deliver_server_message(
            adapter, make_platform_message("Done", message_type=MessageType.TEXT)
        )
        assert (await first).stop_reason == "end_turn"
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_other_room_remains_independent(
        self, mock_rest_client: MagicMock
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        adapter._session_to_room["session-2"] = "room-2"
        adapter._room_to_session["room-2"] = "session-2"
        first = prompt(server)
        second = asyncio.create_task(
            server.prompt(session_id="session-2", prompt=[{"text": "Other"}])
        )
        await wait_for_pending_prompt(adapter, "room-123")
        await wait_for_pending_prompt(adapter, "room-2")
        await deliver_server_message(adapter, failure_event())
        with pytest.raises(RequestError) as raised:
            await first
        assert_default_room_failure(raised.value)
        assert not second.done()
        await deliver_server_message(
            adapter, make_platform_message("Other done", room_id="room-2")
        )
        assert (await second).stop_reason == "end_turn"
