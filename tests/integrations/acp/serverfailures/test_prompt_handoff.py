from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.exceptions import RequestError
from band_sdk_core import AgentFailure

from band.core.types import MessageType
from tests.integrations.acp.conftest import (
    deliver_server_message,
    failure_event,
    make_platform_message,
    wait_for_pending_prompt,
)
from tests.integrations.acp.serverfailures.kit import (
    prompt,
    running_server,
)


class TestPromptHandoff:
    @pytest.mark.asyncio
    async def test_room_event_before_post_does_not_drop_the_prompt(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        release = asyncio.Event()
        entered = asyncio.Event()
        participants = mock_rest_client.agent_api_participants.list_agent_chat_participants.return_value
        push = MagicMock()
        push.handle_push_event = AsyncMock()
        adapter.set_push_handler(push)

        async def delayed_participants(**kwargs: object) -> object:
            entered.set()
            await release.wait()
            return participants

        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(side_effect=delayed_participants)
        )
        task = prompt(server)
        await entered.wait()
        await deliver_server_message(adapter, failure_event())
        assert not task.done()
        push.handle_push_event.assert_awaited_once()
        release.set()
        await wait_for_pending_prompt(adapter, "room-123")
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_awaited()
        await deliver_server_message(
            adapter, make_platform_message("Done", message_type=MessageType.TEXT)
        )
        assert (await task).stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_lookup_failure_is_not_hidden_by_an_earlier_room_event(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        release = asyncio.Event()
        entered = asyncio.Event()

        async def failed_participants(**kwargs: object) -> None:
            entered.set()
            await release.wait()
            raise RuntimeError("REST failed")

        mock_rest_client.agent_api_participants.list_agent_chat_participants = (
            AsyncMock(side_effect=failed_participants)
        )
        task = prompt(server)
        await entered.wait()
        await deliver_server_message(adapter, failure_event())
        assert not task.done()
        release.set()
        with pytest.raises(RequestError) as raised:
            await task
        assert raised.value.code == -32603
        assert (
            raised.value.data == AgentFailure("band", "REST failed").to_extension_data()
        )
        mock_rest_client.agent_api_messages.create_agent_chat_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_previous_turn_text_during_post_stays_unsolicited(
        self,
        mock_rest_client: MagicMock,
    ) -> None:
        adapter, server = running_server(mock_rest_client)
        push = MagicMock()
        push.handle_push_event = AsyncMock()
        adapter.set_push_handler(push)
        entered = asyncio.Event()
        release = asyncio.Event()
        posted = (
            mock_rest_client.agent_api_messages.create_agent_chat_message.return_value
        )

        async def slow_post(**kwargs: object) -> object:
            entered.set()
            await release.wait()
            return posted

        mock_rest_client.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=slow_post
        )
        task = prompt(server)
        await entered.wait()
        straggler = make_platform_message("Previous answer")
        await deliver_server_message(adapter, straggler)
        push.handle_push_event.assert_awaited_once_with(straggler, "room-123")

        release.set()
        await wait_for_pending_prompt(adapter, "room-123")
        assert not task.done()
        await deliver_server_message(adapter, make_platform_message("Done"))
        assert (await task).stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_late_error_after_end_turn_is_pushed_as_unsolicited(
        self, mock_rest_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "band.integrations.acp.server_adapter._PROMPT_COMPLETION_GRACE_SECONDS",
            0.01,
        )
        adapter, server = running_server(mock_rest_client)
        push = MagicMock()
        push.handle_push_event = AsyncMock()
        adapter.set_push_handler(push)
        task = prompt(server)
        await wait_for_pending_prompt(adapter, "room-123")
        await deliver_server_message(
            adapter, make_platform_message("Done", message_type=MessageType.TEXT)
        )
        assert (await task).stop_reason == "end_turn"

        late_error = failure_event()
        await deliver_server_message(adapter, late_error)

        push.handle_push_event.assert_awaited_once_with(late_error, "room-123")
