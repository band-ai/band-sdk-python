from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.exceptions import RequestError

from band.core.types import MessageType
from band.integrations.acp.failure import prompt_timeout_failure
from tests.integrations.acp.conftest import (
    make_tool_call_message,
)
from tests.integrations.acp.serverfailures.kit import (
    assert_default_room_failure,
    prompt,
    running_server,
)
from tests.integrations.acp.serverkit import (
    deliver_server_message,
    failure_event,
    make_platform_message,
    wait_for_pending_prompt,
)


class TestPromptCompletion:
    @pytest.mark.looptime
    async def test_prompt_timeout_bounds_send_and_reply_together(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "band.integrations.acp.server_adapter._PROMPT_TIMEOUT_SECONDS", 10
        )
        _, server = running_server(mock_rest_client)
        participants = mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value

        async def slow_participants(**kwargs: object) -> object:
            await asyncio.sleep(6)
            return participants

        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(side_effect=slow_participants)
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(RequestError) as raised:
            await prompt(server)
        assert loop.time() - started == pytest.approx(10)
        assert raised.value.data == prompt_timeout_failure(10).to_extension_data()

    @pytest.mark.looptime
    async def test_tool_activity_after_text_keeps_the_prompt_open(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS", 1.0
        )
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(
            adapter,
            make_platform_message("Looking into it", message_type=MessageType.TEXT),
        )
        await asyncio.sleep(0.6)
        await deliver_server_message(adapter, make_tool_call_message())
        await asyncio.sleep(0.6)
        assert not task.done()
        await asyncio.sleep(0.6)
        assert task.done()
        assert (await task).stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_text_grace_yields_to_error(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS",
            0.05,
        )
        adapter, server = running_server(mock_rest_client)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(
            adapter, make_platform_message("Part", message_type=MessageType.TEXT)
        )
        await deliver_server_message(
            adapter, make_platform_message("Thinking", message_type=MessageType.THOUGHT)
        )
        await deliver_server_message(adapter, failure_event())
        with pytest.raises(RequestError) as raised:
            await task
        assert_default_room_failure(raised.value)

    @pytest.mark.asyncio
    async def test_old_text_timer_cannot_complete_next_prompt(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS",
            0.01,
        )
        adapter, server = running_server(mock_rest_client)
        first = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(
            adapter, make_platform_message("Part", message_type=MessageType.TEXT)
        )
        await deliver_server_message(adapter, failure_event())
        with pytest.raises(RequestError) as raised:
            await first
        assert_default_room_failure(raised.value)

        second = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await asyncio.sleep(0.03)
        assert not second.done()
        await adapter.cancel_prompt("session-1")
        assert (await second).stop_reason == "cancelled"
